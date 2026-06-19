"""Context budgeting: bound the history replayed upstream every turn instead of
sending the entire transcript (runaway cost / context-window overflow)."""
import json


async def _signup(client):
    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})


async def _consume(client, path, body):
    async with client.stream("POST", path, json=body) as r:
        assert r.status_code == 200, await r.aread()
        async for _ in r.aiter_bytes():
            pass


# ---- pure helpers ----

def test_budget_by_message_count_keeps_newest():
    from app.config import settings
    from app.conversations import _budget_history

    settings.max_context_messages = 3
    settings.max_context_tokens = 0
    hist = [{"role": "user", "content": f"m{i}"} for i in range(10)]
    assert [m["content"] for m in _budget_history(hist)] == ["m7", "m8", "m9"]


def test_budget_by_tokens_always_keeps_at_least_newest():
    from app.config import settings
    from app.conversations import _budget_history

    settings.max_context_messages = 0
    settings.max_context_tokens = 5  # ~20 chars; each message below is ~10 tokens
    hist = [{"role": "user", "content": "x" * 40} for _ in range(5)]
    out = _budget_history(hist)
    # Each message alone exceeds the budget, but the newest is never dropped.
    assert len(out) == 1 and out[0] is hist[-1]


def test_budget_tokens_trims_oldest_to_fit():
    from app.config import settings
    from app.conversations import _budget_history

    settings.max_context_messages = 0
    settings.max_context_tokens = 12  # room for ~2 of the ~5-token messages below
    hist = [{"role": "user", "content": f"{i}:" + "y" * 18} for i in range(6)]  # ~5 tokens each
    out = _budget_history(hist)
    assert 1 <= len(out) <= 3
    assert out[-1] is hist[-1]  # suffix, newest kept
    # contiguous newest suffix (no gaps)
    contents = [m["content"] for m in hist]
    assert [m["content"] for m in out] == contents[len(contents) - len(out):]


def test_budget_disabled_keeps_everything():
    from app.config import settings
    from app.conversations import _budget_history

    settings.max_context_messages = 0
    settings.max_context_tokens = 0
    hist = [{"role": "user", "content": f"m{i}"} for i in range(50)]
    assert _budget_history(hist) == hist


# ---- end-to-end wiring ----

async def test_send_trims_replayed_history(client):
    import httpx
    from app.config import settings
    from app.main import app

    await _signup(client)
    settings.max_context_messages = 2
    settings.max_context_tokens = 0
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    for i in range(4):
        await _consume(client, f"/api/conversations/{cid}/messages", {"content": f"msg{i}"})

    captured: dict = {}

    async def capture(request: httpx.Request) -> httpx.Response:
        from tests.conftest import _fake_chat_stream, _fake_handler
        if request.url.path.endswith("/chat/completions"):
            captured["payload"] = json.loads(request.content)
            return _fake_chat_stream(captured["payload"])
        return await _fake_handler(request)

    app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(capture), base_url="http://upstream/v1"
    )
    await _consume(client, f"/api/conversations/{cid}/messages", {"content": "newest"})

    non_system = [m for m in captured["payload"]["messages"] if m["role"] != "system"]
    # prior history trimmed to the last 2 turns; the current user message is
    # always appended on top -> 2 + 1 = 3 non-system messages.
    assert len(non_system) == 3
    assert non_system[-1]["content"] == "newest"
    # the dropped-oldest turns are gone (msg0/msg1 no longer replayed)
    blob = json.dumps(non_system)
    assert "msg0" not in blob and "msg1" not in blob
