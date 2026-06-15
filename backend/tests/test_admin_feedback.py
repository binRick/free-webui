"""Admin feedback log."""


async def _signup(client):
    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})


async def _consume(client, method, path, body=None):
    async with client.stream(method, path, json=body or {}) as r:
        assert r.status_code == 200
        async for _ in r.aiter_bytes():
            pass


async def test_admin_feedback_lists_ratings(client):
    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", {"content": "hi"})
    conv = (await client.get(f"/api/conversations/{cid}")).json()
    aid = next(m["id"] for m in conv["messages"] if m["role"] == "assistant")
    await client.put(f"/api/conversations/{cid}/messages/{aid}/feedback", json={"rating": 1, "comment": "great"})

    rows = (await client.get("/api/admin/feedback")).json()
    assert len(rows) == 1
    r = rows[0]
    assert r["rating"] == 1 and r["comment"] == "great" and r["username"] == "alice"
    assert r["conversation_id"] == cid and r["snippet"]

    # rating filter
    assert (await client.get("/api/admin/feedback", params={"rating": -1})).json() == []
    assert len((await client.get("/api/admin/feedback", params={"rating": 1})).json()) == 1


async def test_admin_feedback_requires_admin(client):
    await _signup(client)
    await client.post("/api/admin/users", json={"username": "bob", "password": "passpass", "role": "user"})
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": "bob", "password": "passpass"})
    assert (await client.get("/api/admin/feedback")).status_code == 403
