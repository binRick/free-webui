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
