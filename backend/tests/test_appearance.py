"""Instance appearance: public /api/config branding + admin write + access control."""


async def _admin(client):
    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})


async def _make_bob(client):
    await client.post(
        "/api/admin/users", json={"username": "bob", "password": "passpass", "role": "user"}
    )


async def _login(client, username, password="passpass"):
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": username, "password": password})


async def test_config_public_default(client):
    # readable with no auth at all, returns the configured default name.
    r = await client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert body["instance_name"] == "free-webui"
    assert body["custom_css"] == ""


async def test_admin_set_appearance_reflected_in_public_config(client):
    await _admin(client)
    r = await client.put(
        "/api/admin/appearance",
        json={"instance_name": "Acme Chat", "custom_css": ":root{--accent:#f0f}"},
    )
    assert r.status_code == 200
    assert r.json()["instance_name"] == "Acme Chat"

    # the public endpoint now reflects it (even logged out).
    await client.post("/api/auth/logout")
    cfg = (await client.get("/api/config")).json()
    assert cfg["instance_name"] == "Acme Chat"
    assert cfg["custom_css"] == ":root{--accent:#f0f}"


async def test_appearance_name_trimmed_and_validated(client):
    await _admin(client)
    r = await client.put("/api/admin/appearance", json={"instance_name": "  Spaced  "})
    assert r.status_code == 200
    assert r.json()["instance_name"] == "Spaced"
    # empty name is rejected
    assert (await client.put("/api/admin/appearance", json={"instance_name": ""})).status_code == 422
    # ...and a whitespace-only name (would strip to empty) is rejected too
    assert (await client.put("/api/admin/appearance", json={"instance_name": "   "})).status_code == 422


async def test_appearance_css_size_cap(client):
    await _admin(client)
    r = await client.put(
        "/api/admin/appearance",
        json={"instance_name": "X", "custom_css": "a" * 200_000},
    )
    assert r.status_code == 422


async def test_appearance_write_requires_admin(client):
    await _admin(client)
    await _make_bob(client)
    await _login(client, "bob")
    assert (await client.get("/api/admin/appearance")).status_code == 403
    assert (
        await client.put("/api/admin/appearance", json={"instance_name": "Hax"})
    ).status_code == 403
    # ...but a regular user can still read the public config
    assert (await client.get("/api/config")).status_code == 200
