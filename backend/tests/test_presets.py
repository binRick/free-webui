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


async def test_preset_captures_mode_fields(client):
    """A preset is a chat 'mode': it also bundles tools/web-search + a blurb."""
    await _signup(client)
    body = {
        "name": "researcher",
        "model": "qwen2.5:14b",
        "description": "web + tools on",
        "tools_enabled": True,
        "web_search": True,
    }
    created = (await client.post("/api/presets", json=body)).json()
    assert created["tools_enabled"] is True
    assert created["web_search"] is True
    assert created["description"] == "web + tools on"

    row = (await client.get("/api/presets")).json()[0]
    assert row["tools_enabled"] is True
    assert row["web_search"] is True
    assert row["description"] == "web + tools on"


async def test_preset_mode_fields_default_off(client):
    await _signup(client)
    created = (await client.post("/api/presets", json={"name": "plain"})).json()
    assert created["tools_enabled"] is False
    assert created["web_search"] is False
    assert created["description"] is None


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
