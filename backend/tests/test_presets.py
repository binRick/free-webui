async def _signup(client):
    await client.post(
        "/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"}
    )


async def test_preset_round_trip(client):
    await _signup(client)
    body = {
        "name": "terse senior",
        "model": "qwen2.5-coder:14b",
        "system_prompt": "be terse",
        "temperature": 0.3,
        "top_p": 0.9,
        "stop": ["###"],
    }
    created = await client.post("/api/presets", json=body)
    assert created.status_code == 200, created.text
    pid = created.json()["id"]

    listed = await client.get("/api/presets")
    assert len(listed.json()) == 1
    row = listed.json()[0]
    assert row["name"] == "terse senior"
    assert row["stop"] == ["###"]

    assert (await client.delete(f"/api/presets/{pid}")).status_code == 204
    assert (await client.get("/api/presets")).json() == []


async def test_presets_are_per_user(client):
    import time as _t
    from app.auth import hash_password
    from app.main import app

    await _signup(client)
    await client.post(
        "/api/presets", json={"name": "alice-only", "model": "x"}
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
    assert (await client.get("/api/presets")).json() == []
