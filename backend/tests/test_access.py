"""User groups + per-model access control (admin API + enforcement on
/api/models, the chat path, and /v1)."""


async def _admin(client):
    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})


async def _make_bob(client) -> int:
    return (
        await client.post(
            "/api/admin/users", json={"username": "bob", "password": "passpass", "role": "user"}
        )
    ).json()["id"]


async def _login(client, username, password="passpass"):
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": username, "password": password})


async def _model_ids(client):
    return [m["id"] for m in (await client.get("/api/models")).json()["data"]]


async def test_group_crud(client):
    await _admin(client)
    g = (await client.post("/api/admin/groups", json={"name": "team"})).json()
    assert g["name"] == "team" and g["member_count"] == 0
    assert (await client.post("/api/admin/groups", json={"name": "team"})).status_code == 409
    assert len((await client.get("/api/admin/groups")).json()) == 1
    assert (await client.delete(f"/api/admin/groups/{g['id']}")).status_code == 204
    assert (await client.get("/api/admin/groups")).json() == []


async def test_group_membership(client):
    await _admin(client)
    bob = await _make_bob(client)
    g = (await client.post("/api/admin/groups", json={"name": "team"})).json()
    r = await client.put(f"/api/admin/groups/{g['id']}/members", json={"user_ids": [bob, 9999]})
    assert r.json()["user_ids"] == [bob]  # unknown user id is skipped (FK)
    listing = (await client.get("/api/admin/groups")).json()
    assert listing[0]["member_count"] == 1


async def test_restrict_model_to_user_hides_from_others(client):
    await _admin(client)
    await _make_bob(client)
    me = (await client.get("/api/auth/me")).json()
    # restrict fake-a to alice only; fake-b stays public
    await client.put("/api/admin/model_access", json={"model_id": "fake-a", "user_ids": [me["id"]]})

    assert set(await _model_ids(client)) == {"fake-a", "fake-b"}  # admin sees all

    await _login(client, "bob")
    assert await _model_ids(client) == ["fake-b"]  # bob sees only the public model

    # bob cannot chat with fake-a, but can with fake-b
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    blocked = await client.post(
        f"/api/conversations/{cid}/messages", json={"content": "hi", "model": "fake-a"}
    )
    assert blocked.status_code == 403

    async with client.stream(
        "POST", f"/api/conversations/{cid}/messages", json={"content": "hi", "model": "fake-b"}
    ) as r:
        assert r.status_code == 200
        async for _ in r.aiter_bytes():
            pass


async def test_group_grant_allows_member(client):
    await _admin(client)
    bob = await _make_bob(client)
    g = (await client.post("/api/admin/groups", json={"name": "team"})).json()
    await client.put(f"/api/admin/groups/{g['id']}/members", json={"user_ids": [bob]})
    await client.put("/api/admin/model_access", json={"model_id": "fake-a", "group_ids": [g["id"]]})

    await _login(client, "bob")
    assert set(await _model_ids(client)) == {"fake-a", "fake-b"}  # in team -> sees fake-a


async def test_public_again_when_grants_cleared(client):
    await _admin(client)
    await _make_bob(client)
    me = (await client.get("/api/auth/me")).json()
    await client.put("/api/admin/model_access", json={"model_id": "fake-a", "user_ids": [me["id"]]})
    await client.put("/api/admin/model_access", json={"model_id": "fake-a"})  # clear -> public

    await _login(client, "bob")
    assert set(await _model_ids(client)) == {"fake-a", "fake-b"}


async def test_create_and_patch_block_restricted_model(client):
    await _admin(client)
    await _make_bob(client)
    me = (await client.get("/api/auth/me")).json()
    await client.put("/api/admin/model_access", json={"model_id": "fake-a", "user_ids": [me["id"]]})

    await _login(client, "bob")
    # cannot create a conversation pinned to a restricted model
    r = await client.post("/api/conversations", json={"model": "fake-a"})
    assert r.status_code == 403
    # ...nor PATCH an existing conversation onto one
    cid = (await client.post("/api/conversations", json={"model": "fake-b"})).json()["id"]
    r = await client.patch(f"/api/conversations/{cid}", json={"model": "fake-a"})
    assert r.status_code == 403


async def test_autotitle_respects_model_access(client, upstream):
    # fake-a is public when bob starts; an admin restricts it afterwards.
    await _admin(client)
    await _make_bob(client)
    me = (await client.get("/api/auth/me")).json()

    await _login(client, "bob")
    cid = (await client.post("/api/conversations", json={"model": "fake-a"})).json()["id"]
    async with client.stream(
        "POST", f"/api/conversations/{cid}/messages", json={"content": "hi", "model": "fake-a"}
    ) as r:
        assert r.status_code == 200
        async for _ in r.aiter_bytes():
            pass

    await _login(client, "alice", "hunter22hunter")
    await client.put("/api/admin/model_access", json={"model_id": "fake-a", "user_ids": [me["id"]]})

    await _login(client, "bob")
    before = len(upstream.chat_calls)
    r = await client.post(f"/api/conversations/{cid}/autotitle")
    assert r.status_code == 403  # the title endpoint must not run a restricted model
    assert len(upstream.chat_calls) == before  # ...and must not call the upstream


async def test_model_access_fail_closed_on_unknown_ids(client):
    await _admin(client)
    # supplying only ids that don't exist must be refused, not silently public
    r = await client.put(
        "/api/admin/model_access", json={"model_id": "fake-a", "group_ids": [9999], "user_ids": [8888]}
    )
    assert r.status_code == 422
    # fake-a stays public
    await _make_bob(client)
    await _login(client, "bob")
    assert "fake-a" in await _model_ids(client)


async def test_admin_endpoints_require_admin(client):
    await _admin(client)
    await _make_bob(client)
    await _login(client, "bob")
    assert (await client.get("/api/admin/groups")).status_code == 403
    assert (
        await client.put("/api/admin/model_access", json={"model_id": "x"})
    ).status_code == 403


async def test_v1_model_access_enforced(client):
    await _admin(client)
    await _make_bob(client)
    me = (await client.get("/api/auth/me")).json()
    await client.put("/api/admin/model_access", json={"model_id": "fake-a", "user_ids": [me["id"]]})

    await _login(client, "bob")
    key = (await client.post("/api/api_keys", json={"name": "k"})).json()["key"]
    headers = {"Authorization": f"Bearer {key}"}

    # /v1/models is filtered too — the key can't even see the restricted name.
    v1_models = [m["id"] for m in (await client.get("/v1/models", headers=headers)).json()["data"]]
    assert v1_models == ["fake-b"]

    r = await client.post(
        "/v1/chat/completions", headers=headers,
        json={"model": "fake-a", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 403
    assert r.json()["error"]["type"] == "access_denied"

    async with client.stream(
        "POST", "/v1/chat/completions", headers=headers,
        json={"model": "fake-b", "messages": [{"role": "user", "content": "hi"}], "stream": True},
    ) as resp:
        assert resp.status_code == 200  # public model allowed
