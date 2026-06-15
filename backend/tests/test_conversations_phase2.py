"""Phase 2 backend: non-destructive regenerate (branching), message feedback,
and conversation search."""
from tests.conftest import content_chunk, finish, sse


async def _signup(client, username="alice", password="hunter22hunter"):
    await client.post("/api/auth/setup", json={"username": username, "password": password})


async def _consume(client, method, path, body=None):
    async with client.stream(method, path, json=body or {}) as r:
        assert r.status_code == 200, await r.aread()
        async for _ in r.aiter_bytes():
            pass


async def _new(client):
    return (await client.post("/api/conversations", json={})).json()["id"]


# ---- branching: regenerate is non-destructive ----

async def test_regenerate_archives_old_variant(client, upstream):
    await _signup(client)
    cid = await _new(client)
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", {"content": "hi"})

    upstream.queue_chat(sse(content_chunk("second take"), finish("stop")))
    await _consume(client, "POST", f"/api/conversations/{cid}/regenerate")

    # The GET (active-only) shows exactly one user + one assistant, the new one.
    msgs = (await client.get(f"/api/conversations/{cid}")).json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[-1]["content"] == "second take"

    # ...but the prior assistant variant is preserved (active=0), not deleted,
    # and the new variant chains back to it via parent_id.
    from app.main import app

    cur = await app.state.db.execute(
        "SELECT id, content, active, parent_id FROM messages "
        "WHERE conversation_id = ? AND role = 'assistant' ORDER BY id",
        (cid,),
    )
    rows = await cur.fetchall()
    assert len(rows) == 2
    archived = [r for r in rows if r[2] == 0]
    active = [r for r in rows if r[2] == 1]
    assert len(archived) == 1 and archived[0][1].strip() == "echo: hi"
    assert len(active) == 1 and active[0][1] == "second take"
    assert active[0][3] == archived[0][0]  # parent_id -> archived variant id


# ---- feedback ----

async def _assistant_id(client, cid):
    conv = (await client.get(f"/api/conversations/{cid}")).json()
    return next(m["id"] for m in conv["messages"] if m["role"] == "assistant")


async def test_feedback_upsert_and_clear(client):
    await _signup(client)
    cid = await _new(client)
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", {"content": "hi"})
    aid = await _assistant_id(client, cid)

    r = await client.put(f"/api/conversations/{cid}/messages/{aid}/feedback", json={"rating": 1})
    assert r.json()["rating"] == 1
    conv = (await client.get(f"/api/conversations/{cid}")).json()
    assert next(m["rating"] for m in conv["messages"] if m["id"] == aid) == 1

    # Re-rate updates rather than duplicating.
    await client.put(f"/api/conversations/{cid}/messages/{aid}/feedback", json={"rating": -1})
    conv = (await client.get(f"/api/conversations/{cid}")).json()
    assert next(m["rating"] for m in conv["messages"] if m["id"] == aid) == -1

    from app.main import app

    n = (await (await app.state.db.execute("SELECT COUNT(*) FROM message_feedback")).fetchone())[0]
    assert n == 1  # upsert, not a second row

    # Clear.
    r = await client.put(f"/api/conversations/{cid}/messages/{aid}/feedback", json={"rating": 0})
    assert r.json()["rating"] is None
    conv = (await client.get(f"/api/conversations/{cid}")).json()
    assert next(m["rating"] for m in conv["messages"] if m["id"] == aid) is None


async def test_feedback_validation_and_missing_message(client):
    await _signup(client)
    cid = await _new(client)
    # unknown message -> 404
    r = await client.put(f"/api/conversations/{cid}/messages/9999/feedback", json={"rating": 1})
    assert r.status_code == 404
    # rating out of range -> 422
    r = await client.put(f"/api/conversations/{cid}/messages/1/feedback", json={"rating": 5})
    assert r.status_code == 422


async def test_feedback_is_owner_scoped(client):
    import time as _t

    from app.auth import hash_password
    from app.main import app

    await _signup(client, "alice", "passpass")
    cid = await _new(client)
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", {"content": "hi"})
    aid = await _assistant_id(client, cid)

    await app.state.db.execute(
        "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
        ("bob", hash_password("passpass"), "user", int(_t.time())),
    )
    await app.state.db.commit()
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": "bob", "password": "passpass"})

    r = await client.put(f"/api/conversations/{cid}/messages/{aid}/feedback", json={"rating": 1})
    assert r.status_code == 404  # bob cannot rate alice's message


# ---- search ----

async def test_autotitle_generates_from_exchange(client, upstream):
    await _signup(client)
    cid = await _new(client)
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", {"content": "tell me about penguins"})

    # The titling call consumes the next queued upstream response.
    upstream.queue_chat(sse(content_chunk("Penguin Facts"), finish()))
    r = await client.post(f"/api/conversations/{cid}/autotitle")
    assert r.status_code == 200
    assert r.json()["title"] == "Penguin Facts"

    summary = next(c for c in (await client.get("/api/conversations")).json() if c["id"] == cid)
    assert summary["title"] == "Penguin Facts"


async def test_autotitle_noop_without_exchange(client, upstream):
    await _signup(client)
    cid = await _new(client)
    r = await client.post(f"/api/conversations/{cid}/autotitle")
    assert r.status_code == 200
    assert r.json()["title"] == "new chat"
    assert upstream.chat_calls == []  # no upstream call when there's nothing to title


async def test_autotitle_disabled_is_noop(client, upstream, monkeypatch):
    from app import conversations

    await _signup(client)
    cid = await _new(client)
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", {"content": "hi there"})
    monkeypatch.setattr(conversations.settings, "auto_title", False)
    before = len(upstream.chat_calls)
    r = await client.post(f"/api/conversations/{cid}/autotitle")
    assert r.status_code == 200
    assert len(upstream.chat_calls) == before  # disabled -> no upstream call


async def test_conversation_search(client):
    await _signup(client)
    c1 = await _new(client)
    await _consume(client, "POST", f"/api/conversations/{c1}/messages", {"content": "tell me about penguins"})
    c2 = await _new(client)
    await _consume(client, "POST", f"/api/conversations/{c2}/messages", {"content": "quantum computing basics"})

    by_content = [c["id"] for c in (await client.get("/api/conversations", params={"q": "penguin"})).json()]
    assert c1 in by_content and c2 not in by_content

    by_title = [c["id"] for c in (await client.get("/api/conversations", params={"q": "QUANTUM"})).json()]
    assert by_title == [c2]  # case-insensitive

    all_blank = (await client.get("/api/conversations", params={"q": "   "})).json()
    assert len(all_blank) == 2  # blank query returns everything

    none = (await client.get("/api/conversations", params={"q": "zzzzz"})).json()
    assert none == []
