async def _setup_admin(client):
    await client.post(
        "/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"}
    )


async def test_admin_create_list_delete_user(client):
    await _setup_admin(client)
    listed = (await client.get("/api/admin/users")).json()
    assert len(listed) == 1 and listed[0]["username"] == "alice"

    created = await client.post(
        "/api/admin/users",
        json={"username": "bob", "password": "hunter22hunter", "role": "user"},
    )
    assert created.status_code == 200
    bob_id = created.json()["id"]
    assert created.json()["role"] == "user"

    # bob can log in
    await client.post("/api/auth/logout")
    assert (
        await client.post(
            "/api/auth/login", json={"username": "bob", "password": "hunter22hunter"}
        )
    ).status_code == 200
    # bob can't reach admin endpoints
    assert (await client.get("/api/admin/users")).status_code == 403

    # admin deletes bob
    await client.post(
        "/api/auth/login", json={"username": "alice", "password": "hunter22hunter"}
    )
    assert (await client.delete(f"/api/admin/users/{bob_id}")).status_code == 204
    listed = (await client.get("/api/admin/users")).json()
    assert [u["username"] for u in listed] == ["alice"]


async def test_admin_cannot_self_delete(client):
    await _setup_admin(client)
    me = (await client.get("/api/auth/me")).json()
    r = await client.delete(f"/api/admin/users/{me['id']}")
    assert r.status_code == 400


async def test_cannot_delete_or_demote_only_admin(client):
    await _setup_admin(client)
    # Create another user (non-admin) so deletion attempts don't trip the
    # "cannot delete self" guard first.
    bob = (
        await client.post(
            "/api/admin/users",
            json={"username": "bob", "password": "hunter22hunter", "role": "user"},
        )
    ).json()
    # Promote bob to admin via PATCH
    promoted = await client.patch(
        f"/api/admin/users/{bob['id']}", json={"role": "admin"}
    )
    assert promoted.status_code == 200 and promoted.json()["role"] == "admin"

    # Demote bob back; alice is still admin so that's fine
    demoted = await client.patch(
        f"/api/admin/users/{bob['id']}", json={"role": "user"}
    )
    assert demoted.status_code == 200 and demoted.json()["role"] == "user"

    # Now demoting alice (the only remaining admin) must fail
    me = (await client.get("/api/auth/me")).json()
    refused = await client.patch(f"/api/admin/users/{me['id']}", json={"role": "user"})
    assert refused.status_code == 400


async def test_admin_can_disable_and_enable_user(client):
    await _setup_admin(client)
    bob = (
        await client.post(
            "/api/admin/users",
            json={"username": "bob", "password": "hunter22hunter"},
        )
    ).json()

    # bob has a working API key + a live cookie session before suspension
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": "bob", "password": "hunter22hunter"})
    key = (await client.post("/api/api_keys", json={"name": "k"})).json()["key"]
    bearer = {"Authorization": f"Bearer {key}"}
    assert (await client.get("/v1/models", headers=bearer)).status_code == 200
    bob_cookie = client.cookies.get("fw_session")

    # admin disables bob
    await client.post("/api/auth/login", json={"username": "alice", "password": "hunter22hunter"})
    r = await client.patch(f"/api/admin/users/{bob['id']}", json={"disabled": True})
    assert r.status_code == 200 and r.json()["disabled"] is True
    assert any(u["id"] == bob["id"] and u["disabled"] for u in (await client.get("/api/admin/users")).json())

    # every door is now closed for bob: login, API key, and the old cookie
    assert (
        await client.post("/api/auth/login", json={"username": "bob", "password": "hunter22hunter"})
    ).status_code == 403
    assert (await client.get("/v1/models", headers=bearer)).status_code == 403
    # the pre-suspension cookie is cut too (token_version was bumped)
    client.cookies.clear()
    client.cookies.set("fw_session", bob_cookie, domain="testserver")
    assert (await client.get("/api/auth/me")).status_code in (401, 403)

    # admin re-enables bob -> login works again, data intact
    client.cookies.clear()
    await client.post("/api/auth/login", json={"username": "alice", "password": "hunter22hunter"})
    r = await client.patch(f"/api/admin/users/{bob['id']}", json={"disabled": False})
    assert r.status_code == 200 and r.json()["disabled"] is False
    assert (
        await client.post("/api/auth/login", json={"username": "bob", "password": "hunter22hunter"})
    ).status_code == 200
    # the API key still works after re-enable (suspension never destroyed it)
    assert (await client.get("/v1/models", headers=bearer)).status_code == 200


async def test_disabled_check_is_authoritative_in_current_user(client):
    # current_user is the single gate: a disabled flag blocks a live, otherwise
    # valid cookie session even when token_version was NOT bumped (defense
    # against any future path that suspends without revoking sessions).
    await _setup_admin(client)
    await client.post(
        "/api/admin/users", json={"username": "bob", "password": "hunter22hunter"}
    )
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": "bob", "password": "hunter22hunter"})
    assert (await client.get("/api/auth/me")).status_code == 200  # live valid session

    from app.main import app

    await app.state.db.execute("UPDATE users SET disabled = 1 WHERE username = 'bob'")
    await app.state.db.commit()

    r = await client.get("/api/auth/me")  # same cookie, tv still matches
    assert r.status_code == 403 and r.json()["detail"] == "account disabled"


async def test_cannot_disable_self(client):
    await _setup_admin(client)
    me = (await client.get("/api/auth/me")).json()
    r = await client.patch(f"/api/admin/users/{me['id']}", json={"disabled": True})
    assert r.status_code == 400


async def test_disable_writes_audit_log(client):
    await _setup_admin(client)
    bob = (
        await client.post(
            "/api/admin/users", json={"username": "bob", "password": "hunter22hunter"}
        )
    ).json()
    await client.patch(f"/api/admin/users/{bob['id']}", json={"disabled": True})
    await client.patch(f"/api/admin/users/{bob['id']}", json={"disabled": False})
    actions = [e["action"] for e in (await client.get("/api/admin/audit")).json()]
    assert "user.disable" in actions and "user.enable" in actions


async def test_admin_can_reset_password(client):
    await _setup_admin(client)
    bob = (
        await client.post(
            "/api/admin/users",
            json={"username": "bob", "password": "originalpw1"},
        )
    ).json()
    r = await client.patch(
        f"/api/admin/users/{bob['id']}", json={"password": "newerpw1234"}
    )
    assert r.status_code == 200
    await client.post("/api/auth/logout")
    assert (
        await client.post(
            "/api/auth/login", json={"username": "bob", "password": "originalpw1"}
        )
    ).status_code == 401
    assert (
        await client.post(
            "/api/auth/login", json={"username": "bob", "password": "newerpw1234"}
        )
    ).status_code == 200
