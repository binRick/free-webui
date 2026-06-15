"""Public read-only conversation share links."""


async def _signup(client, username="alice", password="hunter22hunter"):
    await client.post("/api/auth/setup", json={"username": username, "password": password})


async def _consume(client, method, path, body=None):
    async with client.stream(method, path, json=body or {}) as r:
        assert r.status_code == 200
        async for _ in r.aiter_bytes():
            pass


async def _conv_with_message(client) -> str:
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", {"content": "hello world"})
    return cid


async def test_share_create_get_revoke_and_public_read(client):
    await _signup(client)
    cid = await _conv_with_message(client)

    assert (await client.get(f"/api/conversations/{cid}/share")).json()["token"] is None

    token = (await client.post(f"/api/conversations/{cid}/share")).json()["token"]
    assert token
    # idempotent: same token returned
    assert (await client.post(f"/api/conversations/{cid}/share")).json()["token"] == token
    assert (await client.get(f"/api/conversations/{cid}/share")).json()["token"] == token

    # public read (no auth header needed — the test client just hits the path)
    shared = (await client.get(f"/api/shared/{token}")).json()
    assert shared["title"]
    roles = [m["role"] for m in shared["messages"]]
    assert roles == ["user", "assistant"]
    assert shared["messages"][0]["content"] == "hello world"

    # revoke -> public link 404s
    assert (await client.delete(f"/api/conversations/{cid}/share")).status_code == 204
    assert (await client.get(f"/api/shared/{token}")).status_code == 404


async def test_shared_unknown_token_404(client):
    await _signup(client)
    assert (await client.get("/api/shared/nope")).status_code == 404


async def test_share_is_owner_scoped(client):
    import time as _t

    from app.auth import hash_password
    from app.main import app

    await _signup(client, "alice", "passpass")
    cid = await _conv_with_message(client)

    await app.state.db.execute(
        "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
        ("bob", hash_password("passpass"), "user", int(_t.time())),
    )
    await app.state.db.commit()
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": "bob", "password": "passpass"})

    assert (await client.post(f"/api/conversations/{cid}/share")).status_code == 404
    assert (await client.get(f"/api/conversations/{cid}/share")).status_code == 404


async def test_share_disabled_by_config(client, monkeypatch):
    from app import shares

    await _signup(client)
    cid = await _conv_with_message(client)
    monkeypatch.setattr(shares.settings, "allow_public_sharing", False)
    assert (await client.post(f"/api/conversations/{cid}/share")).status_code == 403
