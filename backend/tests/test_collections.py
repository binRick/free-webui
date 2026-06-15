"""Knowledge-base collections: CRUD, document ingest, conversation attachment,
and RAG retrieval over an attached collection."""


async def _signup(client, username="alice", password="hunter22hunter"):
    await client.post("/api/auth/setup", json={"username": username, "password": password})


def _file(text: str, name: str = "kb.txt"):
    return {"file": (name, text.encode(), "text/plain")}


async def test_collection_crud_and_documents(client):
    await _signup(client)
    c = (await client.post("/api/collections", json={"name": "handbook"})).json()
    assert c["name"] == "handbook" and c["document_count"] == 0

    up = await client.post(f"/api/collections/{c['id']}/documents", files=_file("the sky is green on tuesdays"))
    assert up.status_code == 200 and up.json()["chunk_count"] >= 1

    docs = (await client.get(f"/api/collections/{c['id']}/documents")).json()
    assert len(docs) == 1

    listed = (await client.get("/api/collections")).json()
    assert listed[0]["document_count"] == 1

    assert (await client.delete(f"/api/collections/{c['id']}/documents/{up.json()['id']}")).status_code == 204
    assert (await client.get(f"/api/collections/{c['id']}/documents")).json() == []

    assert (await client.delete(f"/api/collections/{c['id']}")).status_code == 204
    assert (await client.get("/api/collections")).json() == []


async def test_collection_is_owner_scoped(client):
    import time as _t

    from app.auth import hash_password
    from app.main import app

    await _signup(client, "alice", "passpass")
    c = (await client.post("/api/collections", json={"name": "alice-kb"})).json()

    await app.state.db.execute(
        "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
        ("bob", hash_password("passpass"), "user", int(_t.time())),
    )
    await app.state.db.commit()
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": "bob", "password": "passpass"})

    assert (await client.get(f"/api/collections/{c['id']}/documents")).status_code == 404
    assert (await client.delete(f"/api/collections/{c['id']}")).status_code == 404
    assert (await client.get("/api/collections")).json() == []


async def test_attach_collection_filters_unowned(client):
    await _signup(client)
    c = (await client.post("/api/collections", json={"name": "kb"})).json()
    conv = (await client.post("/api/conversations", json={})).json()["id"]

    # attach the owned collection + a bogus id -> only the owned one sticks
    r = await client.put(f"/api/conversations/{conv}/collections", json={"collection_ids": [c["id"], 99999]})
    assert r.json()["collection_ids"] == [c["id"]]
    assert (await client.get(f"/api/conversations/{conv}/collections")).json()["collection_ids"] == [c["id"]]

    # detach
    await client.put(f"/api/conversations/{conv}/collections", json={"collection_ids": []})
    assert (await client.get(f"/api/conversations/{conv}/collections")).json()["collection_ids"] == []


async def test_attached_collection_is_searched_in_rag(client):
    from app.rag import retrieve_context
    from app.main import app

    await _signup(client)
    c = (await client.post("/api/collections", json={"name": "kb"})).json()
    await client.post(f"/api/collections/{c['id']}/documents", files=_file("Penguins huddle to stay warm."))
    conv = (await client.post("/api/conversations", json={})).json()["id"]

    # before attaching: no context from the collection
    ctx = await retrieve_context(app.state.db, app.state.http, conv, "penguins")
    assert ctx is None

    await client.put(f"/api/conversations/{conv}/collections", json={"collection_ids": [c["id"]]})
    ctx = await retrieve_context(app.state.db, app.state.http, conv, "penguins")
    assert ctx is not None and "Penguins huddle" in ctx
