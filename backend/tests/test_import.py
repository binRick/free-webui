"""Conversation import: recreate an exported chat as a new conversation."""
import json


async def _signup(client):
    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})


async def _consume(client, path, body):
    async with client.stream("POST", path, json=body) as r:
        async for _ in r.aiter_lines():
            pass


async def test_export_import_roundtrip(client):
    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    await _consume(client, f"/api/conversations/{cid}/messages", {"content": "Hello world"})
    # give it some settings to carry over
    await client.patch(
        f"/api/conversations/{cid}",
        json={"system_prompt": "be terse", "temperature": 0.4, "stop": ["END"]},
    )

    exported = json.loads((await client.get(f"/api/conversations/{cid}/export?format=json")).content)

    # import the exact exported JSON (id/timestamps/etc. ignored) → a NEW conversation
    new = (await client.post("/api/conversations/import", json=exported)).json()
    assert new["id"] != cid

    got = (await client.get(f"/api/conversations/{new['id']}")).json()
    assert got["system_prompt"] == "be terse"
    assert got["temperature"] == 0.4
    assert got["stop"] == ["END"]
    roles = [(m["role"], m["content"]) for m in got["messages"]]
    assert roles[0] == ("user", "Hello world")
    assert roles[1][0] == "assistant"


async def test_import_minimal_payload(client):
    await _signup(client)
    payload = {
        "title": "my import",
        "messages": [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
        ],
    }
    new = (await client.post("/api/conversations/import", json=payload)).json()
    assert new["title"] == "my import"
    got = (await client.get(f"/api/conversations/{new['id']}")).json()
    assert [(m["role"], m["content"]) for m in got["messages"]] == [
        ("user", "q1"), ("assistant", "a1"),
    ]


async def test_import_drops_inaccessible_model(client):
    # restrict a model to nobody-but-admin... actually restrict to a different user,
    # then import as a regular user with that model -> model dropped, not a 403.
    await _signup(client)  # alice = admin
    await client.post(
        "/api/admin/users", json={"username": "bob", "password": "passpass", "role": "user"}
    )
    me = (await client.get("/api/auth/me")).json()
    await client.put("/api/admin/model_access", json={"model_id": "fake-a", "user_ids": [me["id"]]})

    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": "bob", "password": "passpass"})
    r = await client.post(
        "/api/conversations/import",
        json={"title": "t", "model": "fake-a", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200
    assert r.json()["model"] is None  # restricted model dropped, import still succeeds


async def test_import_drops_unresolved_image_refs(client):
    await _signup(client)
    payload = {
        "title": "imgs",
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": "look"},
                {"type": "image_url", "image_url": {"url": "/api/files/deadbeef"}},
            ]},
        ],
    }
    new = (await client.post("/api/conversations/import", json=payload)).json()
    got = (await client.get(f"/api/conversations/{new['id']}")).json()
    # the unresolvable file ref is dropped; the text survives (collapsed to a string)
    assert got["messages"][0]["content"] == "look"


async def test_import_skips_bad_roles(client):
    await _signup(client)
    payload = {"messages": [
        {"role": "tool", "content": "ignored"},
        {"role": "user", "content": "kept"},
    ]}
    new = (await client.post("/api/conversations/import", json=payload)).json()
    got = (await client.get(f"/api/conversations/{new['id']}")).json()
    assert [m["role"] for m in got["messages"]] == ["user"]


async def test_import_requires_auth(client):
    await _signup(client)
    await client.post("/api/auth/logout")
    r = await client.post("/api/conversations/import", json={"messages": []})
    assert r.status_code == 401
