"""Admin-broadcast banners."""


async def _signup(client):
    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})


async def _make_user(client, username):
    return (
        await client.post(
            "/api/admin/users", json={"username": username, "password": "passpass", "role": "user"}
        )
    ).json()["id"]


async def _login(client, username, password="passpass"):
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": username, "password": password})


async def test_banner_lifecycle(client):
    await _signup(client)  # alice is admin
    assert (await client.get("/api/banners")).json() == []

    created = (
        await client.post(
            "/api/admin/banners",
            json={"content": "maintenance at 5pm", "type": "warning", "dismissible": True},
        )
    ).json()
    assert created["content"] == "maintenance at 5pm"
    assert created["type"] == "warning"
    assert created["dismissible"] is True

    # every authenticated user sees active banners
    await _make_user(client, "bob")
    await _login(client, "bob")
    banners = (await client.get("/api/banners")).json()
    assert [b["content"] for b in banners] == ["maintenance at 5pm"]

    # only admins can delete
    assert (await client.delete(f"/api/admin/banners/{created['id']}")).status_code == 403

    await client.post("/api/auth/login", json={"username": "alice", "password": "hunter22hunter"})
    assert (await client.delete(f"/api/admin/banners/{created['id']}")).status_code == 204
    assert (await client.get("/api/banners")).json() == []


async def test_banner_admin_only_and_validation(client):
    # unauthenticated can't read or write
    assert (await client.get("/api/banners")).status_code == 401
    assert (await client.post("/api/admin/banners", json={"content": "x"})).status_code == 401

    await _signup(client)
    # non-admin can't create
    await _make_user(client, "bob")
    await _login(client, "bob")
    assert (await client.post("/api/admin/banners", json={"content": "x"})).status_code == 403

    await client.post("/api/auth/login", json={"username": "alice", "password": "hunter22hunter"})
    # validation: empty content, bad type
    assert (await client.post("/api/admin/banners", json={"content": ""})).status_code == 422
    assert (
        await client.post("/api/admin/banners", json={"content": "x", "type": "bogus"})
    ).status_code == 422
