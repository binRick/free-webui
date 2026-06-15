"""Admin audit log + the request-id middleware."""


async def _admin(client):
    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})


async def test_audit_records_admin_actions(client):
    await _admin(client)
    bob = (await client.post("/api/admin/users", json={"username": "bob", "password": "passpass", "role": "user"})).json()
    await client.patch(f"/api/admin/users/{bob['id']}", json={"role": "admin"})
    await client.put("/api/admin/model_access", json={"model_id": "fake-a", "user_ids": []})
    await client.post("/api/admin/groups", json={"name": "team"})

    entries = (await client.get("/api/admin/audit")).json()
    actions = [e["action"] for e in entries]
    assert "user.create" in actions
    assert "user.role_change" in actions
    assert "model_access.set" in actions
    assert "group.create" in actions
    # newest first, and the acting admin is recorded
    assert [e["id"] for e in entries] == sorted((e["id"] for e in entries), reverse=True)
    assert all(e["username"] == "alice" for e in entries)


async def test_audit_requires_admin(client):
    await _admin(client)
    await client.post("/api/admin/users", json={"username": "bob", "password": "passpass", "role": "user"})
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": "bob", "password": "passpass"})
    assert (await client.get("/api/admin/audit")).status_code == 403


async def test_request_id_header(client):
    r = await client.get("/api/health")
    assert r.headers.get("x-request-id")
    r2 = await client.get("/api/health", headers={"x-request-id": "my-trace-123"})
    assert r2.headers.get("x-request-id") == "my-trace-123"
