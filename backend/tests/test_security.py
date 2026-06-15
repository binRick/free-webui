"""Security baseline: /api/models auth, security headers, cookie Secure flag,
request body cap, and login throttling."""


async def _signup(client, username="alice", password="hunter22hunter"):
    return await client.post(
        "/api/auth/setup", json={"username": username, "password": password}
    )


async def test_models_requires_auth(client):
    r = await client.get("/api/models")
    assert r.status_code == 401
    await _signup(client)
    r2 = await client.get("/api/models")
    assert r2.status_code == 200
    assert [m["id"] for m in r2.json()["data"]] == ["fake-a", "fake-b"]


async def test_security_headers_present(client):
    r = await client.get("/api/health")
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"
    assert "frame-ancestors" in (r.headers.get("content-security-policy") or "")
    assert r.headers.get("referrer-policy") == "no-referrer"


async def test_cookie_secure_flag(client, monkeypatch):
    from app import auth

    monkeypatch.setattr(auth.settings, "cookie_secure", True)
    r = await _signup(client)
    assert "Secure" in r.headers.get("set-cookie", "")


async def test_cookie_insecure_by_default(client):
    r = await _signup(client)
    assert "Secure" not in r.headers.get("set-cookie", "")


async def test_body_size_limit_returns_413(client, monkeypatch):
    from app import main

    monkeypatch.setattr(main.settings, "max_request_body_bytes", 50)
    r = await client.post(
        "/api/auth/setup", json={"username": "alice", "password": "x" * 200}
    )
    assert r.status_code == 413


async def test_password_reset_revokes_existing_session(client):
    await _signup(client)  # alice (admin), logged in
    assert (await client.get("/api/auth/me")).status_code == 200
    me = (await client.get("/api/auth/me")).json()

    # Admin resets the password -> token_version bumps -> the live cookie is stale.
    r = await client.patch(f"/api/admin/users/{me['id']}", json={"password": "newpassword123"})
    assert r.status_code == 200
    assert (await client.get("/api/auth/me")).status_code == 401

    # Logging in with the new password issues a fresh, valid session.
    await client.post("/api/auth/logout")
    lr = await client.post("/api/auth/login", json={"username": "alice", "password": "newpassword123"})
    assert lr.status_code == 200
    assert (await client.get("/api/auth/me")).status_code == 200


async def test_logout_all_revokes_session(client):
    await _signup(client)
    assert (await client.get("/api/auth/me")).status_code == 200
    assert (await client.post("/api/auth/logout_all")).status_code == 204
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_login_rate_limit(client, monkeypatch):
    from app import auth

    monkeypatch.setattr(auth.settings, "login_rate_limit", 3)
    monkeypatch.setattr(auth.settings, "login_rate_window_seconds", 60.0)
    auth._login_attempts.clear()

    await _signup(client)  # creates the admin
    codes = []
    for _ in range(5):
        r = await client.post(
            "/api/auth/login", json={"username": "alice", "password": "wrong-pw"}
        )
        codes.append(r.status_code)
    assert codes[:3] == [401, 401, 401]  # first N attempts allowed (and rejected)
    assert codes[3] == 429  # then throttled
    assert codes[4] == 429
