"""Tests for the pipelines / plugin framework (backend/app/plugins.py).

Three layers:

* the loader — discovery, priority ordering, hook validation, and per-file
  isolation of bad plugins (import errors, sync hooks, no hooks, bad PRIORITY);
* the inlet/outlet runners — mutation, deep-copy isolation, per-hook timeout,
  return-type checks, ordering (inlet ascending, outlet reversed);
* the end-to-end wiring through the streaming chat path — an inlet's body edits
  reach the upstream AND survive a multi-iteration tool loop (the regression
  guard for the one-shot `body` refactor), an outlet rewrites only the persisted
  text, a throwing plugin never breaks the turn, and the admin `GET /api/plugins`
  endpoint (with 401/403 gating).
"""
from __future__ import annotations

import json
import tempfile
import textwrap
from pathlib import Path

import httpx
import pytest


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _write_plugins(**files: str) -> str:
    """Materialize {stem: source} into a fresh temp dir; return its path.

    Each source is `textwrap.dedent`-ed so callers can indent the plugin body
    to match the surrounding test code.
    """
    d = Path(tempfile.mkdtemp(prefix="fw-plugins-"))
    for stem, src in files.items():
        (d / f"{stem}.py").write_text(textwrap.dedent(src))
    return str(d)


def _load(**files: str):
    """Write the given plugins to a temp dir and load them through the real
    loader, returning the resulting registry."""
    from app.plugins import load

    return load(_write_plugins(**files))


def _ctx():
    from app.plugins import PluginContext

    return PluginContext(
        db=None, http=None, user_id=1, conversation_id="cid-test", model="m"
    )


@pytest.fixture
def fast_timeout():
    """Shrink the per-hook timeout (on the exact settings object plugins.py
    bound) so timeout tests finish fast; restore it afterwards."""
    import app.plugins as plugins_mod

    original = plugins_mod.settings.plugins_timeout_seconds
    plugins_mod.settings.plugins_timeout_seconds = 0.1
    try:
        yield 0.1
    finally:
        plugins_mod.settings.plugins_timeout_seconds = original


async def _signup(client, username="alice", password="hunter22hunter"):
    return await client.post(
        "/api/auth/setup", json={"username": username, "password": password}
    )


async def _consume_stream(client, method, path, json_body=None):
    """Drive a streaming chat endpoint and return the assembled delta text."""
    text = ""
    async with client.stream(method, path, json=json_body or {}) as r:
        assert r.status_code == 200, await r.aread()
        async for line in r.aiter_lines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            payload = json.loads(data)
            delta = payload.get("choices", [{}])[0].get("delta", {}).get("content")
            if isinstance(delta, str):
                text += delta
    return text


def _sse(chunks: list[dict]) -> httpx.Response:
    body = b"".join(f"data: {json.dumps(c)}\n\n".encode() for c in chunks)
    body += b"data: [DONE]\n\n"
    return httpx.Response(
        200, content=body, headers={"content-type": "text/event-stream"}
    )


# --------------------------------------------------------------------------
# loader
# --------------------------------------------------------------------------

def test_load_disabled_when_unset():
    from app.plugins import load

    for val in ("", None):
        reg = load(val)
        assert reg.records() == []
        assert bool(reg) is False


def test_load_missing_dir_is_not_fatal():
    from app.plugins import load

    reg = load("/no/such/free-webui/plugins/dir")
    assert reg.records() == []
    assert bool(reg) is False


def test_load_orders_inlets_by_priority_then_name():
    reg = _load(
        gamma="PRIORITY = 10\nasync def inlet(b, c): return b\n",
        alpha="PRIORITY = 10\nasync def inlet(b, c): return b\n",
        beta="PRIORITY = 5\nasync def inlet(b, c): return b\n",
    )
    # ascending by (priority, name): beta(5), then the p=10 pair tie-broken by name
    assert [p.name for p in reg.inlets] == ["beta", "alpha", "gamma"]


def test_load_runs_outlets_in_reverse_order():
    reg = _load(
        a="PRIORITY = 1\nasync def outlet(t, c): return t\n",
        b="PRIORITY = 2\nasync def outlet(t, c): return t\n",
        c="PRIORITY = 3\nasync def outlet(t, c): return t\n",
    )
    # outlets nest symmetrically: highest priority unwinds first
    assert [p.name for p in reg.outlets] == ["c", "b", "a"]


def test_load_skips_underscore_files():
    reg = _load(
        _helper="async def inlet(b, c): return b\n",
        real="async def inlet(b, c): return b\n",
    )
    assert [r.name for r in reg.records()] == ["real"]


def test_load_rejects_sync_hooks():
    reg = _load(bad="def inlet(b, c): return b\ndef outlet(t, c): return t\n")
    recs = reg.records()
    assert len(recs) == 1
    r = recs[0]
    assert r.name == "bad"
    assert r.has_inlet is False and r.has_outlet is False
    assert "inlet must be an async function" in r.error
    assert "outlet must be an async function" in r.error
    assert bool(reg) is False  # nothing usable was registered


def test_load_keeps_async_hook_when_sibling_is_sync():
    reg = _load(mixed="async def inlet(b, c): return b\ndef outlet(t, c): return t\n")
    r = reg.records()[0]
    assert r.has_inlet is True and r.has_outlet is False
    assert "outlet must be an async function" in r.error
    assert bool(reg) is True


def test_load_records_import_error_without_crashing():
    reg = _load(
        broken="raise RuntimeError('boom at import')\nasync def inlet(b, c): return b\n",
        good="async def inlet(b, c): return b\n",
    )
    recs = {r.name: r for r in reg.records()}
    assert "import failed" in recs["broken"].error
    assert recs["broken"].has_inlet is False and recs["broken"].has_outlet is False
    assert recs["good"].error is None and recs["good"].has_inlet is True


def test_load_ignores_module_with_no_hooks():
    reg = _load(
        nada="X = 1\ndef helper(): return 2\n",
        real="async def inlet(b, c): return b\n",
    )
    # a clean module exposing no hooks is silently ignored (not even recorded)
    assert [r.name for r in reg.records()] == ["real"]


def test_load_bad_priority_defaults_to_zero():
    reg = _load(weird="PRIORITY = 'not-an-int'\nasync def inlet(b, c): return b\n")
    assert reg.records()[0].priority == 0


def test_load_missing_priority_defaults_to_zero():
    reg = _load(plain="async def inlet(b, c): return b\n")
    assert reg.records()[0].priority == 0


def test_record_shape_and_bool():
    reg = _load(
        full="""
        PRIORITY = 7
        async def inlet(body, ctx): return body
        async def outlet(text, ctx): return text
        """,
    )
    r = reg.records()[0]
    assert (r.name, r.priority, r.has_inlet, r.has_outlet, r.error) == (
        "full",
        7,
        True,
        True,
        None,
    )
    assert bool(reg) is True


# --------------------------------------------------------------------------
# run_inlet
# --------------------------------------------------------------------------

async def test_run_inlet_none_registry_passthrough():
    from app.plugins import run_inlet

    body = {"model": "m", "messages": []}
    assert await run_inlet(None, body, _ctx()) is body


async def test_run_inlet_empty_registry_passthrough():
    from app.plugins import load, run_inlet

    body = {"model": "m"}
    assert await run_inlet(load(""), body, _ctx()) is body


async def test_run_inlet_commits_in_place_mutation_on_none_return():
    from app.plugins import run_inlet

    reg = _load(
        m="""
        async def inlet(body, ctx):
            body['temperature'] = 0.5
            # implicit return None -> the mutated COPY is committed
        """,
    )
    original = {"model": "m"}
    out = await run_inlet(reg, original, _ctx())
    assert out["temperature"] == 0.5
    assert "temperature" not in original  # plugin worked on a deep copy


async def test_run_inlet_commits_dict_return():
    from app.plugins import run_inlet

    reg = _load(
        m="""
        async def inlet(body, ctx):
            return {"model": "replaced", "messages": []}
        """,
    )
    out = await run_inlet(reg, {"model": "m"}, _ctx())
    assert out == {"model": "replaced", "messages": []}


async def test_run_inlet_isolates_throwing_plugin_and_continues():
    from app.plugins import run_inlet

    reg = _load(
        a_boom="""
        PRIORITY = 1
        async def inlet(body, ctx):
            body['temperature'] = 99  # must NOT leak (mutated a copy, then raised)
            raise RuntimeError('boom')
        """,
        b_ok="""
        PRIORITY = 2
        async def inlet(body, ctx):
            body['top_p'] = 0.9
            return body
        """,
    )
    out = await run_inlet(reg, {"model": "m"}, _ctx())
    assert "temperature" not in out  # discarded with the failed plugin
    assert out["top_p"] == 0.9       # the later plugin still ran


async def test_run_inlet_timeout_is_isolated(fast_timeout):
    from app.plugins import run_inlet

    reg = _load(
        slow="""
        import asyncio
        async def inlet(body, ctx):
            await asyncio.sleep(5)
            body['temperature'] = 1
            return body
        """,
    )
    out = await run_inlet(reg, {"model": "m"}, _ctx())
    assert "temperature" not in out  # timed out -> skipped, body untouched


async def test_run_inlet_wrong_type_return_ignored():
    from app.plugins import run_inlet

    reg = _load(
        m="""
        async def inlet(body, ctx):
            return ["not", "a", "dict"]
        """,
    )
    out = await run_inlet(reg, {"model": "m"}, _ctx())
    assert out == {"model": "m"}  # non-dict return ignored


async def test_run_inlet_applies_plugins_in_priority_order():
    from app.plugins import run_inlet

    reg = _load(
        first="""
        PRIORITY = 1
        async def inlet(body, ctx):
            body.setdefault('trace', []).append('first')
            return body
        """,
        second="""
        PRIORITY = 2
        async def inlet(body, ctx):
            body.setdefault('trace', []).append('second')
            return body
        """,
    )
    out = await run_inlet(reg, {"model": "m"}, _ctx())
    # ascending priority, and the second plugin observes the first's commit
    assert out["trace"] == ["first", "second"]


# --------------------------------------------------------------------------
# run_outlet
# --------------------------------------------------------------------------

async def test_run_outlet_none_registry_passthrough():
    from app.plugins import run_outlet

    assert await run_outlet(None, "hi", _ctx()) == "hi"


async def test_run_outlet_applies_in_reverse_order():
    from app.plugins import run_outlet

    reg = _load(
        first="""
        PRIORITY = 1
        async def outlet(text, ctx):
            return text + '[first]'
        """,
        second="""
        PRIORITY = 2
        async def outlet(text, ctx):
            return text + '[second]'
        """,
    )
    # reverse priority: second (p2) wraps before first (p1)
    assert await run_outlet(reg, "x", _ctx()) == "x[second][first]"


async def test_run_outlet_isolates_throwing_plugin():
    from app.plugins import run_outlet

    reg = _load(
        boom="""
        PRIORITY = 1
        async def outlet(text, ctx):
            raise RuntimeError('boom')
        """,
        ok="""
        PRIORITY = 2
        async def outlet(text, ctx):
            return text + '!'
        """,
    )
    # ok (p2) runs first in reverse and appends '!'; boom raises -> skipped
    assert await run_outlet(reg, "hi", _ctx()) == "hi!"


async def test_run_outlet_timeout_is_isolated(fast_timeout):
    from app.plugins import run_outlet

    reg = _load(
        slow="""
        import asyncio
        async def outlet(text, ctx):
            await asyncio.sleep(5)
            return 'NEVER'
        """,
    )
    assert await run_outlet(reg, "kept", _ctx()) == "kept"


async def test_run_outlet_wrong_type_return_ignored():
    from app.plugins import run_outlet

    reg = _load(
        m="""
        async def outlet(text, ctx):
            return 123  # not a str -> ignored
        """,
    )
    assert await run_outlet(reg, "kept", _ctx()) == "kept"


async def test_run_outlet_none_return_is_observe_only():
    from app.plugins import run_outlet

    reg = _load(
        m="""
        async def outlet(text, ctx):
            return None  # observe only; text unchanged
        """,
    )
    assert await run_outlet(reg, "kept", _ctx()) == "kept"


async def test_run_outlet_empty_string_is_committed():
    """An outlet may legitimately return "" — run_outlet commits it (the chat
    path then decides whether an empty reply is persisted)."""
    from app.plugins import run_outlet

    reg = _load(
        m="""
        async def outlet(text, ctx):
            return ""
        """,
    )
    assert await run_outlet(reg, "kept", _ctx()) == ""


# --------------------------------------------------------------------------
# off-contract inlet bodies are discarded (isolation hardening)
# --------------------------------------------------------------------------

async def test_run_inlet_discards_non_list_messages():
    """A non-list `messages` would crash the tool loop's msgs.append; run_inlet
    must discard such an off-contract body and keep the previous one."""
    from app.plugins import run_inlet

    reg = _load(
        m="""
        async def inlet(body, ctx):
            body['messages'] = "oops, not a list"
            return body
        """,
    )
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    out = await run_inlet(reg, body, _ctx())
    assert out["messages"] == [{"role": "user", "content": "hi"}]  # original kept


async def test_run_inlet_discards_non_str_model():
    from app.plugins import run_inlet

    reg = _load(
        m="""
        async def inlet(body, ctx):
            body['model'] = 12345  # not a str
            return body
        """,
    )
    out = await run_inlet(reg, {"model": "m", "messages": []}, _ctx())
    assert out["model"] == "m"  # off-contract model rejected


async def test_run_inlet_tolerates_partial_dict_missing_keys():
    """A partial dict (no messages/model) is committed at the run_inlet layer;
    the chat-path call site re-asserts the model and keeps the message list."""
    from app.plugins import run_inlet

    reg = _load(
        m="""
        async def inlet(body, ctx):
            return {"temperature": 0.7}
        """,
    )
    out = await run_inlet(reg, {"model": "m", "messages": []}, _ctx())
    assert out == {"temperature": 0.7}


# --------------------------------------------------------------------------
# end-to-end regressions for the review fixes
# --------------------------------------------------------------------------

async def test_inlet_partial_dict_preserves_model_and_messages_upstream(client):
    """An inlet that returns a partial dict (dropping model/messages) must not
    blank the upstream request — the chat path re-asserts model and keeps the
    message list, while the inlet's actual edit (temperature) still lands."""
    from app.main import app
    from app.plugins import load
    from tests.conftest import _fake_handler

    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]

    app.state.plugins = load(
        _write_plugins(
            partial="""
            async def inlet(body, ctx):
                return {"temperature": 0.5}  # drops model + messages on purpose
            """
        )
    )

    seen = {}

    async def handler(request):
        if request.url.path.endswith("/chat/completions"):
            seen.update(json.loads(request.content))
        return await _fake_handler(request)

    app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://upstream/v1"
    )

    text = await _consume_stream(
        client, "POST", f"/api/conversations/{cid}/messages", {"content": "hi"}
    )
    assert text.strip() == "echo: hi"
    assert seen.get("temperature") == 0.5            # inlet edit landed
    assert isinstance(seen.get("model"), str) and seen["model"]  # model preserved
    assert isinstance(seen.get("messages"), list) and seen["messages"]  # msgs kept


async def test_inlet_injected_message_survives_tool_loop(client):
    """An inlet that prepends a system message must have it reach the upstream on
    EVERY tool-loop iteration, alongside the loop's appended tool messages."""
    from app.main import app
    from app.plugins import load
    from tests.conftest import _fake_handler

    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    await client.patch(f"/api/conversations/{cid}", json={"tools_enabled": True})

    app.state.plugins = load(
        _write_plugins(
            prepend="""
            async def inlet(body, ctx):
                body['messages'] = [{"role": "system", "content": "SENTINEL"}] + body['messages']
                return body
            """
        )
    )

    seen = []
    calls = {"n": 0}

    async def handler(request):
        if not request.url.path.endswith("/chat/completions"):
            return await _fake_handler(request)
        calls["n"] += 1
        seen.append(json.loads(request.content)["messages"])
        if calls["n"] == 1:
            return _sse(
                [
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_1",
                                            "type": "function",
                                            "function": {
                                                "name": "calculate",
                                                "arguments": '{"expression": "2+3"}',
                                            },
                                        }
                                    ]
                                }
                            }
                        ]
                    },
                    {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
                ]
            )
        return _sse(
            [
                {"choices": [{"delta": {"content": "five"}}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            ]
        )

    app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://upstream/v1"
    )

    text = await _consume_stream(
        client, "POST", f"/api/conversations/{cid}/messages", {"content": "2+3?"}
    )
    assert calls["n"] == 2
    assert text.strip() == "five"
    # the inlet's sentinel reached BOTH iterations
    for msgs in seen:
        assert any(m.get("content") == "SENTINEL" for m in msgs)
    # and the 2nd iteration also carries the loop's appended tool result
    assert any(m.get("role") == "tool" for m in seen[1])


async def test_inlet_non_list_messages_does_not_crash_tool_loop(client):
    """The off-contract body (non-list messages) is discarded, so a tools-enabled
    turn proceeds normally instead of crashing the stream generator."""
    from app.main import app
    from app.plugins import load
    from tests.conftest import _fake_handler

    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    await client.patch(f"/api/conversations/{cid}", json={"tools_enabled": True})

    app.state.plugins = load(
        _write_plugins(
            corrupt="""
            async def inlet(body, ctx):
                body['messages'] = "not a list"  # off-contract; must be discarded
                return body
            """
        )
    )

    calls = {"n": 0}

    async def handler(request):
        if not request.url.path.endswith("/chat/completions"):
            return await _fake_handler(request)
        calls["n"] += 1
        if calls["n"] == 1:
            return _sse(
                [
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_1",
                                            "type": "function",
                                            "function": {
                                                "name": "calculate",
                                                "arguments": '{"expression": "2+3"}',
                                            },
                                        }
                                    ]
                                }
                            }
                        ]
                    },
                    {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
                ]
            )
        return _sse(
            [
                {"choices": [{"delta": {"content": "five"}}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            ]
        )

    app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://upstream/v1"
    )

    text = await _consume_stream(
        client, "POST", f"/api/conversations/{cid}/messages", {"content": "2+3?"}
    )
    assert calls["n"] == 2
    assert text.strip() == "five"  # turn completed; no crash
    full = await client.get(f"/api/conversations/{cid}")
    assert full.json()["messages"][-1]["content"].strip() == "five"


async def test_errored_turn_with_footer_outlet_persists_no_phantom(client):
    """When the upstream errors and produces no content, an outlet that always
    appends a footer must NOT fabricate a phantom assistant message."""
    from app.main import app
    from app.plugins import load

    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]

    app.state.plugins = load(
        _write_plugins(
            footer="""
            async def outlet(text, ctx):
                return text + "\\n\\n_generated by free-webui_"
            """
        )
    )

    async def handler(request):
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(
                400,
                content=b'{"error": "upstream boom"}',
                headers={"content-type": "application/json"},
            )
        from tests.conftest import _fake_handler

        return await _fake_handler(request)

    app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://upstream/v1"
    )

    await _consume_stream(
        client, "POST", f"/api/conversations/{cid}/messages", {"content": "hi"}
    )
    full = await client.get(f"/api/conversations/{cid}")
    roles = [m["role"] for m in full.json()["messages"]]
    assert "assistant" not in roles  # no phantom message from the footer outlet


async def test_outlet_emptying_real_reply_is_not_persisted(client):
    """An outlet that collapses a real reply to "" leaves nothing persisted
    (documented edge: the streamed text already went out verbatim)."""
    from app.main import app
    from app.plugins import load

    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]

    app.state.plugins = load(
        _write_plugins(
            blank="""
            async def outlet(text, ctx):
                return ""
            """
        )
    )

    streamed = await _consume_stream(
        client, "POST", f"/api/conversations/{cid}/messages", {"content": "hello"}
    )
    assert streamed.strip() == "echo: hello"  # client still saw the raw reply
    full = await client.get(f"/api/conversations/{cid}")
    assert "assistant" not in [m["role"] for m in full.json()["messages"]]


# --------------------------------------------------------------------------
# end-to-end wiring through the streaming chat path
# --------------------------------------------------------------------------

async def test_inlet_reaches_upstream_body(client):
    """An inlet's parameter edit lands in the request the backend POSTs upstream."""
    from app.main import app
    from app.plugins import load
    from tests.conftest import _fake_handler

    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]

    app.state.plugins = load(
        _write_plugins(
            season="""
            PRIORITY = 5
            async def inlet(body, ctx):
                body['temperature'] = 0.123
                return body
            """
        )
    )

    seen = {}

    async def handler(request):
        if request.url.path.endswith("/chat/completions"):
            seen["temperature"] = json.loads(request.content).get("temperature")
        return await _fake_handler(request)

    app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://upstream/v1"
    )

    text = await _consume_stream(
        client, "POST", f"/api/conversations/{cid}/messages", {"content": "hi"}
    )
    assert text.strip() == "echo: hi"
    assert seen["temperature"] == 0.123


async def test_inlet_survives_two_iteration_tool_loop(client):
    """Regression guard for the one-shot `body` refactor: an inlet's edits must
    persist across EVERY tool-loop iteration, and the loop (not the inlet) must
    own the tool catalogue (inlet-injected `tools`/`tool_choice` are stripped)."""
    from app.main import app
    from app.plugins import load
    from tests.conftest import _fake_handler

    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    await client.patch(f"/api/conversations/{cid}", json={"tools_enabled": True})

    app.state.plugins = load(
        _write_plugins(
            inject="""
            PRIORITY = 5
            async def inlet(body, ctx):
                body['temperature'] = 0.123
                # These must be discarded — the tool loop owns tool wiring.
                body['tools'] = [{'type': 'function', 'function': {'name': 'BOGUS'}}]
                body['tool_choice'] = 'none'
                return body
            """
        )
    )

    seen = []
    calls = {"n": 0}

    async def handler(request):
        if not request.url.path.endswith("/chat/completions"):
            return await _fake_handler(request)
        calls["n"] += 1
        body = json.loads(request.content)
        seen.append(
            {
                "temperature": body.get("temperature"),
                "tool_names": [
                    t.get("function", {}).get("name") for t in body.get("tools", [])
                ],
            }
        )
        if calls["n"] == 1:
            return _sse(
                [
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_1",
                                            "type": "function",
                                            "function": {
                                                "name": "calculate",
                                                "arguments": '{"expression": "2+3"}',
                                            },
                                        }
                                    ]
                                }
                            }
                        ]
                    },
                    {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
                ]
            )
        return _sse(
            [
                {"choices": [{"delta": {"content": "five"}}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            ]
        )

    app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://upstream/v1"
    )

    text = await _consume_stream(
        client, "POST", f"/api/conversations/{cid}/messages", {"content": "2+3?"}
    )
    assert calls["n"] == 2
    assert text.strip() == "five"
    # The inlet's temperature edit reached BOTH upstream iterations...
    assert [s["temperature"] for s in seen] == [0.123, 0.123]
    # ...and the loop owns tools: the bogus injection was stripped, the real
    # built-in `calculate` tool was offered on both iterations.
    for s in seen:
        assert "BOGUS" not in s["tool_names"]
        assert "calculate" in s["tool_names"]


async def test_outlet_rewrites_only_persisted_text(client):
    """The outlet runs after streaming, so the client still sees the raw text,
    but the stored assistant message is the rewritten one."""
    from app.main import app
    from app.plugins import load

    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]

    app.state.plugins = load(
        _write_plugins(
            shout="""
            async def outlet(text, ctx):
                return text.upper()
            """
        )
    )

    streamed = await _consume_stream(
        client, "POST", f"/api/conversations/{cid}/messages", {"content": "hello"}
    )
    assert streamed.strip() == "echo: hello"  # raw upstream text was streamed

    full = await client.get(f"/api/conversations/{cid}")
    msgs = full.json()["messages"]
    assert msgs[-1]["role"] == "assistant"
    assert msgs[-1]["content"].strip() == "ECHO: HELLO"  # persisted = rewritten


async def test_throwing_plugin_does_not_break_the_turn(client):
    """A plugin whose inlet and outlet both raise is fully isolated: the turn
    completes and persists the unmodified reply."""
    from app.main import app
    from app.plugins import load

    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]

    app.state.plugins = load(
        _write_plugins(
            boom="""
            async def inlet(body, ctx):
                raise RuntimeError('inlet boom')
            async def outlet(text, ctx):
                raise RuntimeError('outlet boom')
            """
        )
    )

    text = await _consume_stream(
        client, "POST", f"/api/conversations/{cid}/messages", {"content": "hello"}
    )
    assert text.strip() == "echo: hello"

    full = await client.get(f"/api/conversations/{cid}")
    msgs = full.json()["messages"]
    assert msgs[-1]["content"].strip() == "echo: hello"  # unmodified


async def test_admin_get_plugins(client):
    """GET /api/plugins lists loaded records for admins, 401 for anon, 403 for
    non-admin users."""
    import time as _t

    from app.auth import hash_password
    from app.main import app
    from app.plugins import load

    # anonymous -> 401 (before any user/state exists)
    assert (await client.get("/api/plugins")).status_code == 401

    await _signup(client)  # alice becomes admin
    app.state.plugins = load(
        _write_plugins(
            alpha="PRIORITY = 20\nasync def inlet(body, ctx): return body\n",
            beta="PRIORITY = 10\nasync def outlet(text, ctx): return text\n",
        )
    )

    r = await client.get("/api/plugins")
    assert r.status_code == 200
    recs = r.json()
    # sorted by (priority, name): beta(10) before alpha(20)
    assert [x["name"] for x in recs] == ["beta", "alpha"]
    assert recs[0] == {
        "name": "beta",
        "priority": 10,
        "has_inlet": False,
        "has_outlet": True,
        "error": None,
    }
    assert recs[1] == {
        "name": "alpha",
        "priority": 20,
        "has_inlet": True,
        "has_outlet": False,
        "error": None,
    }

    # a non-admin user is forbidden
    await app.state.db.execute(
        "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
        ("bob", hash_password("hunter22hunter"), "user", int(_t.time())),
    )
    await app.state.db.commit()
    await client.post("/api/auth/logout")
    await client.post(
        "/api/auth/login", json={"username": "bob", "password": "hunter22hunter"}
    )
    assert (await client.get("/api/plugins")).status_code == 403
