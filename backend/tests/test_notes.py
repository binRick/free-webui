"""Per-user markdown notes: CRUD, partial update, ownership isolation."""


async def _signup(client, username="alice", password="hunter22hunter"):
    await client.post("/api/auth/setup", json={"username": username, "password": password})


async def _make_bob(client):
    await client.post(
        "/api/admin/users", json={"username": "bob", "password": "passpass", "role": "user"}
    )


async def _login(client, username, password):
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": username, "password": password})


async def test_note_crud(client):
    await _signup(client)
    assert (await client.get("/api/notes")).json() == []

    created = (await client.post("/api/notes", json={"title": "Ideas", "content": "# todo"})).json()
    nid = created["id"]
    assert created["title"] == "Ideas" and created["content"] == "# todo"

    got = (await client.get(f"/api/notes/{nid}")).json()
    assert got["content"] == "# todo"

    # partial update: change content only, title preserved
    r = await client.patch(f"/api/notes/{nid}", json={"content": "# todo\n- ship notes"})
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "Ideas"
    assert body["content"] == "# todo\n- ship notes"
    assert body["updated_at"] >= created["updated_at"]

    assert [n["id"] for n in (await client.get("/api/notes")).json()] == [nid]
    assert (await client.delete(f"/api/notes/{nid}")).status_code == 204
    assert (await client.get(f"/api/notes/{nid}")).status_code == 404


async def test_notes_default_empty_content(client):
    await _signup(client)
    created = (await client.post("/api/notes", json={"title": "Blank"})).json()
    assert created["content"] == ""


async def test_notes_are_user_isolated(client):
    await _signup(client)
    nid = (await client.post("/api/notes", json={"title": "Secret"})).json()["id"]
    await _make_bob(client)
    await _login(client, "bob", "passpass")
    # bob sees none of alice's notes and can't read/patch/delete them
    assert (await client.get("/api/notes")).json() == []
    assert (await client.get(f"/api/notes/{nid}")).status_code == 404
    assert (await client.patch(f"/api/notes/{nid}", json={"title": "hax"})).status_code == 404
    assert (await client.delete(f"/api/notes/{nid}")).status_code == 404


async def test_note_title_required(client):
    await _signup(client)
    assert (await client.post("/api/notes", json={"content": "no title"})).status_code == 422
    assert (await client.post("/api/notes", json={"title": ""})).status_code == 422
