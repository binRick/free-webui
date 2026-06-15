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
    assert created["collection_ids"] == []


# ---- custom assistants: bundled knowledge ----

async def _make_collection(client, name="kb"):
    return (await client.post("/api/collections", json={"name": name})).json()["id"]


async def test_preset_bundles_knowledge(client):
    await _signup(client)
    c1 = await _make_collection(client, "alpha")
    c2 = await _make_collection(client, "beta")
    created = (
        await client.post(
            "/api/presets", json={"name": "researcher", "collection_ids": [c1, c2]}
        )
    ).json()
    assert sorted(created["collection_ids"]) == sorted([c1, c2])
    # round-trips through the list endpoint
    row = (await client.get("/api/presets")).json()[0]
    assert sorted(row["collection_ids"]) == sorted([c1, c2])


async def test_preset_filters_foreign_collections(client):
    """A preset must never bundle another user's collection."""
    await _signup(client)
    mine = await _make_collection(client, "mine")
    # bob owns a collection
    await client.post(
        "/api/admin/users", json={"username": "bob", "password": "passpass", "role": "user"}
    )
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": "bob", "password": "passpass"})
    bob_coll = await _make_collection(client, "bob-secret")
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": "alice", "password": "hunter22hunter"})

    created = (
        await client.post(
            "/api/presets", json={"name": "x", "collection_ids": [mine, bob_coll, 9999]}
        )
    ).json()
    # only alice's own collection survives the ownership filter
    assert created["collection_ids"] == [mine]


async def test_preset_update_replaces_knowledge_and_fields(client):
    await _signup(client)
    c1 = await _make_collection(client, "one")
    c2 = await _make_collection(client, "two")
    pid = (
        await client.post("/api/presets", json={"name": "v1", "collection_ids": [c1]})
    ).json()["id"]

    r = await client.put(
        f"/api/presets/{pid}",
        json={"name": "v2", "tools_enabled": True, "collection_ids": [c2]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "v2"
    assert body["tools_enabled"] is True
    assert body["collection_ids"] == [c2]

    # not another user's preset
    await client.post(
        "/api/admin/users", json={"username": "bob", "password": "passpass", "role": "user"}
    )
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": "bob", "password": "passpass"})
    assert (await client.put(f"/api/presets/{pid}", json={"name": "hax"})).status_code == 404


async def test_delete_preset_clears_knowledge_links(client):
    await _signup(client)
    c1 = await _make_collection(client)
    pid = (
        await client.post("/api/presets", json={"name": "doomed", "collection_ids": [c1]})
    ).json()["id"]
    await client.delete(f"/api/presets/{pid}")
    # the collection itself survives; deleting it again is a clean no-op
    assert (await client.delete(f"/api/collections/{c1}")).status_code == 204


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
