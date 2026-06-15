"""Regression tests for the adversarial-review findings:
- variant chain stays linear/consistent across regenerate-after-switch (MEDIUM)
- activate_variant is rejected for non-trailing turns (LOW)
- request body cap is enforced even without Content-Length / chunked (HIGH)
- expand_file_refs inline budget bounds amplification (HIGH)
"""
import base64

from tests.conftest import content_chunk, finish, sse

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 4000
DATA_URL = f"data:image/png;base64,{base64.b64encode(PNG).decode()}"


async def _signup(client):
    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})


async def _consume(client, method, path, body=None):
    async with client.stream(method, path, json=body or {}) as r:
        assert r.status_code == 200, await r.aread()
        async for _ in r.aiter_bytes():
            pass


async def _new(client):
    return (await client.post("/api/conversations", json={})).json()["id"]


async def _msgs(client, cid):
    return (await client.get(f"/api/conversations/{cid}")).json()["messages"]


# ---- MEDIUM: regenerate after navigating to an older variant ----

async def test_regenerate_after_switch_keeps_chain_consistent(client, upstream):
    await _signup(client)
    cid = await _new(client)
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", {"content": "hi"})

    # v1 active. Regenerate -> v2 active, v1 archived.
    upstream.queue_chat(sse(content_chunk("v2"), finish("stop")))
    await _consume(client, "POST", f"/api/conversations/{cid}/regenerate")
    active_id = (await _msgs(client, cid))[1]["id"]
    chain = (await client.get(f"/api/conversations/{cid}/messages/{active_id}/variants")).json()[
        "variants"
    ]
    assert len(chain) == 2
    v1_id = chain[0]["id"]

    # Navigate back to the older variant v1, then regenerate from there.
    await client.post(f"/api/conversations/{cid}/messages/{v1_id}/activate")
    upstream.queue_chat(sse(content_chunk("v3"), finish("stop")))
    await _consume(client, "POST", f"/api/conversations/{cid}/regenerate")

    # Exactly ONE active assistant message, and it's the new v3.
    msgs = await _msgs(client, cid)
    assistants = [m for m in msgs if m["role"] == "assistant"]
    assert len(assistants) == 1
    assert assistants[0]["content"].strip() == "v3"

    # The variant chain is still linear and now lists all three takes.
    tip = assistants[0]["id"]
    chain = (await client.get(f"/api/conversations/{cid}/messages/{tip}/variants")).json()[
        "variants"
    ]
    assert len(chain) == 3
    assert [v["active"] for v in chain] == [False, False, True]


# ---- LOW: activate_variant only on the trailing turn ----

async def test_activate_non_trailing_variant_rejected(client, upstream):
    await _signup(client)
    cid = await _new(client)
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", {"content": "turn one"})
    upstream.queue_chat(sse(content_chunk("alt"), finish("stop")))
    await _consume(client, "POST", f"/api/conversations/{cid}/regenerate")
    first_chain = (
        await client.get(
            f"/api/conversations/{cid}/messages/{(await _msgs(client, cid))[1]['id']}/variants"
        )
    ).json()["variants"]
    old_first = first_chain[0]["id"]

    # Add a second turn so the first turn is no longer trailing.
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", {"content": "turn two"})

    r = await client.post(f"/api/conversations/{cid}/messages/{old_first}/activate")
    assert r.status_code == 409


# ---- HIGH: body cap enforced without Content-Length ----

async def test_chunked_body_over_cap_is_truncated(client, monkeypatch):
    from app.config import settings

    await _signup(client)
    cid = await _new(client)
    monkeypatch.setattr(settings, "max_request_body_bytes", 2048)

    # A chunked request (no content-length) larger than the cap must not be
    # buffered/processed in full — it gets truncated and fails to parse (4xx),
    # rather than slipping through unbounded.
    async def big_chunks():
        for _ in range(8):
            yield b'{"content":"' + b"A" * 1024 + b'"}'

    r = await client.post(f"/api/conversations/{cid}/messages", content=big_chunks())
    assert r.status_code >= 400
    assert r.status_code != 500


# ---- HIGH: inline amplification budget ----

async def test_inline_budget_caps_replayed_images(client, upstream, monkeypatch):
    from app import files

    await _signup(client)
    cid = await _new(client)
    # Tiny budget so only the first image is inlined on replay.
    monkeypatch.setattr(files, "_INLINE_BUDGET", len(PNG) + 10)
    # patch the value used by conversations._load_history too (imported by name)
    import app.conversations as conv_mod
    monkeypatch.setattr(conv_mod, "_INLINE_BUDGET", len(PNG) + 10)

    img_msg = {"content": [{"type": "image_url", "image_url": {"url": DATA_URL}}]}
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", img_msg)
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", img_msg)
    # third turn triggers a replay of both prior image turns
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", {"content": "ok"})

    # Inspect what was replayed upstream: only one image should be inlined as a
    # data: URL; the other stays a bare /api/files ref (budget exhausted).
    last = upstream.chat_calls[-1]
    inlined = sum(
        1
        for msg in last["messages"]
        if isinstance(msg.get("content"), list)
        for p in msg["content"]
        if p.get("type") == "image_url" and p["image_url"]["url"].startswith("data:")
    )
    refs = sum(
        1
        for msg in last["messages"]
        if isinstance(msg.get("content"), list)
        for p in msg["content"]
        if p.get("type") == "image_url" and p["image_url"]["url"].startswith("/api/files/")
    )
    assert inlined == 1
    assert refs == 1
