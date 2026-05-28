async def test_setup_required_on_fresh_db(client):
    r = await client.get("/api/auth/status")
    assert r.status_code == 200
    assert r.json() == {"user": None, "setup_required": True}


async def test_setup_creates_admin_and_logs_in(client):
    r = await client.post(
        "/api/auth/setup", json={"username": "alice", "password": "hunter22"}
    )
    assert r.status_code == 200
    assert r.json()["username"] == "alice"
    assert r.json()["role"] == "admin"
    # Cookie persists across the AsyncClient instance.
    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "alice"


async def test_setup_blocks_when_user_exists(client):
    await client.post(
        "/api/auth/setup", json={"username": "alice", "password": "hunter22"}
    )
    r = await client.post(
        "/api/auth/setup", json={"username": "mallory", "password": "hunter22"}
    )
    assert r.status_code == 409


async def test_login_round_trip(client):
    await client.post(
        "/api/auth/setup", json={"username": "alice", "password": "hunter22"}
    )
    await client.post("/api/auth/logout")
    # Without cookie, /me is 401.
    me = await client.get("/api/auth/me")
    assert me.status_code == 401
    # Bad password is 401.
    bad = await client.post(
        "/api/auth/login", json={"username": "alice", "password": "wrong"}
    )
    assert bad.status_code == 401
    # Good password succeeds + sets cookie.
    ok = await client.post(
        "/api/auth/login", json={"username": "alice", "password": "hunter22"}
    )
    assert ok.status_code == 200
    me2 = await client.get("/api/auth/me")
    assert me2.status_code == 200


async def test_conversations_require_auth(client):
    r = await client.get("/api/conversations")
    assert r.status_code == 401
    r = await client.post("/api/conversations", json={"model": None})
    assert r.status_code == 401
