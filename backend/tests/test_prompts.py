async def _signup(client):
    await client.post(
        "/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"}
    )


async def test_prompts_require_auth(client):
    r = await client.get("/api/prompts")
    assert r.status_code == 401


async def test_prompt_crud(client):
    await _signup(client)
    assert (await client.get("/api/prompts")).json() == []

    created = await client.post(
        "/api/prompts", json={"title": "summarise", "content": "summarise the above in 3 bullets"}
    )
    assert created.status_code == 200
    pid = created.json()["id"]

    listed = await client.get("/api/prompts")
    assert len(listed.json()) == 1

    patched = await client.patch(
        f"/api/prompts/{pid}", json={"title": "summarise-v2"}
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "summarise-v2"
    assert patched.json()["content"] == "summarise the above in 3 bullets"

    assert (await client.delete(f"/api/prompts/{pid}")).status_code == 204
    assert (await client.get("/api/prompts")).json() == []


async def test_prompts_are_per_user(client):
    """A second user must not see the first user's prompts."""
    import time as _t
    from app.auth import hash_password
    from app.main import app

    await _signup(client)
    await client.post(
        "/api/prompts", json={"title": "alice-private", "content": "secrets"}
    )

    await app.state.db.execute(
        "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
        ("bob", hash_password("hunter22hunter"), "user", int(_t.time())),
    )
    await app.state.db.commit()

    await client.post("/api/auth/logout")
    await client.post(
        "/api/auth/login", json={"username": "bob", "password": "hunter22hunter"}
    )
    assert (await client.get("/api/prompts")).json() == []
