"""Self-service account: data export + account deletion."""
import json


async def _signup(client, username="alice", password="hunter22hunter"):
    await client.post("/api/auth/setup", json={"username": username, "password": password})


async def _consume(client, path, body):
    async with client.stream("POST", path, json=body) as r:
        assert r.status_code == 200
        async for _ in r.aiter_bytes():
            pass


async def test_export_bundles_user_data(client):
    await _signup(client)
    # produce some data across a few tables
    cid = (await client.post("/api/conversations", json={"model": "m"})).json()["id"]
    await _consume(client, f"/api/conversations/{cid}/messages", {"content": "hello there"})
    await client.post("/api/prompts", json={"title": "p1", "content": "do x"})
    await client.post("/api/notes", json={"title": "n1", "content": "note body"})

    r = await client.get("/api/account/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert "attachment" in r.headers.get("content-disposition", "")
    data = json.loads(r.content)

    assert data["profile"]["username"] == "alice"
    assert [c["id"] for c in data["conversations"]] == [cid]
    assert any(m["content"] == "hello there" for m in data["messages"])
    assert any(p["title"] == "p1" for p in data["prompts"])
    assert any(n["title"] == "n1" for n in data["notes"])
    # the export must never leak key material
    blob = r.content.decode()
    assert "key_hash" not in blob and "password_hash" not in blob


async def test_export_is_scoped_to_the_caller(client):
    await _signup(client)  # alice (admin)
    acid = (await client.post("/api/conversations", json={})).json()["id"]
    # a second user with their own conversation
    await client.post("/api/admin/users", json={"username": "bob", "password": "passpass", "role": "user"})
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": "bob", "password": "passpass"})
    bcid = (await client.post("/api/conversations", json={})).json()["id"]

    data = json.loads((await client.get("/api/account/export")).content)
    ids = [c["id"] for c in data["conversations"]]
    assert bcid in ids and acid not in ids  # only bob's data
    assert data["profile"]["username"] == "bob"


async def test_export_requires_auth(client):
    assert (await client.get("/api/account/export")).status_code == 401


async def test_delete_account_requires_correct_password(client):
    await _signup(client)
    await client.post("/api/admin/users", json={"username": "bob", "password": "passpass", "role": "user"})
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": "bob", "password": "passpass"})

    # wrong password -> 401, account survives
    r = await client.request("DELETE", "/api/account", json={"password": "nope"})
    assert r.status_code == 401
    assert (await client.get("/api/auth/me")).status_code == 200


async def test_delete_account_removes_user_and_data(client):
    await _signup(client)
    await client.post("/api/admin/users", json={"username": "bob", "password": "passpass", "role": "user"})
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": "bob", "password": "passpass"})
    me = (await client.get("/api/auth/me")).json()
    cid = (await client.post("/api/conversations", json={})).json()["id"]

    r = await client.request("DELETE", "/api/account", json={"password": "passpass"})
    assert r.status_code == 200 and r.json()["deleted"] is True

    # session cleared + user gone; cannot log back in
    assert (await client.get("/api/auth/me")).status_code == 401
    login = await client.post("/api/auth/login", json={"username": "bob", "password": "passpass"})
    assert login.status_code == 401

    # their conversation cascaded away
    from app.main import app
    db = app.state.db
    n = (await (await db.execute("SELECT COUNT(*) FROM conversations WHERE id = ?", (cid,))).fetchone())[0]
    assert n == 0
    _ = me


async def test_delete_account_blocks_only_admin(client):
    await _signup(client)  # alice is the sole admin
    r = await client.request("DELETE", "/api/account", json={"password": "hunter22hunter"})
    assert r.status_code == 400
    assert (await client.get("/api/auth/me")).status_code == 200  # still here
