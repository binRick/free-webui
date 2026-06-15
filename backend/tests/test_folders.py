"""Conversation folders: CRUD, move-into/out-of, list filter, ownership, and
un-filing on folder delete."""


async def _signup(client):
    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})


async def _consume(client, cid, text):
    async with client.stream(
        "POST", f"/api/conversations/{cid}/messages", json={"content": text}
    ) as r:
        assert r.status_code == 200, await r.aread()
        async for _ in r.aiter_bytes():
            pass


async def _new_with_msg(client, text="hi"):
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    await _consume(client, cid, text)
    return cid


async def test_folder_crud(client):
    await _signup(client)
    assert (await client.get("/api/folders")).json() == []

    f = (await client.post("/api/folders", json={"name": "Work"})).json()
    assert f["name"] == "Work"
    fid = f["id"]
    assert [x["name"] for x in (await client.get("/api/folders")).json()] == ["Work"]

    r = await client.patch(f"/api/folders/{fid}", json={"name": "Personal"})
    assert r.status_code == 200 and r.json()["name"] == "Personal"

    assert (await client.delete(f"/api/folders/{fid}")).status_code == 204
    assert (await client.get("/api/folders")).json() == []


async def test_move_into_and_out_of_folder_and_filter(client):
    await _signup(client)
    fid = (await client.post("/api/folders", json={"name": "Work"})).json()["id"]
    a = await _new_with_msg(client, "one")
    b = await _new_with_msg(client, "two")

    # move only `a` into the folder
    r = await client.patch(f"/api/conversations/{a}", json={"folder_id": fid})
    assert r.status_code == 200
    assert next(c for c in (await client.get("/api/conversations")).json() if c["id"] == a)[
        "folder_id"
    ] == fid

    # filter to the folder -> only `a`
    listed = (await client.get(f"/api/conversations?folder_id={fid}")).json()
    assert [c["id"] for c in listed] == [a]

    # remove `a` from the folder (explicit null)
    r = await client.patch(f"/api/conversations/{a}", json={"folder_id": None})
    assert r.status_code == 200
    assert (await client.get(f"/api/conversations?folder_id={fid}")).json() == []
    # b was never filed
    assert next(c for c in (await client.get("/api/conversations")).json() if c["id"] == b)[
        "folder_id"
    ] is None


async def test_patch_without_folder_id_leaves_it_unchanged(client):
    await _signup(client)
    fid = (await client.post("/api/folders", json={"name": "Work"})).json()["id"]
    a = await _new_with_msg(client)
    await client.patch(f"/api/conversations/{a}", json={"folder_id": fid})
    # a patch that doesn't mention folder_id must not clear it
    await client.patch(f"/api/conversations/{a}", json={"title": "Renamed"})
    summary = next(c for c in (await client.get("/api/conversations")).json() if c["id"] == a)
    assert summary["folder_id"] == fid
    assert summary["title"] == "Renamed"


async def test_delete_folder_unfiles_conversations(client):
    await _signup(client)
    fid = (await client.post("/api/folders", json={"name": "Work"})).json()["id"]
    a = await _new_with_msg(client)
    await client.patch(f"/api/conversations/{a}", json={"folder_id": fid})

    await client.delete(f"/api/folders/{fid}")
    # the conversation survives, just un-filed
    summary = next(c for c in (await client.get("/api/conversations")).json() if c["id"] == a)
    assert summary["folder_id"] is None


async def test_move_into_unknown_or_foreign_folder_404(client):
    await _signup(client)
    a = await _new_with_msg(client)
    assert (await client.patch(f"/api/conversations/{a}", json={"folder_id": 9999})).status_code == 404

    # bob's folder is not usable by alice
    await client.post(
        "/api/admin/users", json={"username": "bob", "password": "passpass", "role": "user"}
    )
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": "bob", "password": "passpass"})
    bob_folder = (await client.post("/api/folders", json={"name": "Bob"})).json()["id"]
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": "alice", "password": "hunter22hunter"})
    assert (
        await client.patch(f"/api/conversations/{a}", json={"folder_id": bob_folder})
    ).status_code == 404
