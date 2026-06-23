"""First-class cross-conversation tag management: list-with-counts, rename
(with merge), and delete-everywhere. Per-conversation tag editing is covered
elsewhere; here we drive /api/tags."""


async def _signup(client, username="alice", password="hunter22hunter"):
    await client.post("/api/auth/setup", json={"username": username, "password": password})


async def _conv_with_tags(client, tags):
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    await client.put(f"/api/conversations/{cid}/tags", json={"tags": tags})
    return cid


async def test_list_tags_with_counts(client):
    await _signup(client)
    await _conv_with_tags(client, ["work", "urgent"])
    await _conv_with_tags(client, ["work", "personal"])

    tags = (await client.get("/api/tags")).json()
    counts = {t["tag"]: t["count"] for t in tags}
    assert counts == {"work": 2, "urgent": 1, "personal": 1}
    # most-used first
    assert tags[0]["tag"] == "work"


async def test_rename_tag_across_conversations(client):
    await _signup(client)
    c1 = await _conv_with_tags(client, ["draft", "idea"])
    c2 = await _conv_with_tags(client, ["draft"])

    r = await client.post("/api/tags/rename", json={"old": "draft", "new": "wip"})
    assert r.status_code == 200 and r.json() == {"tag": "wip", "count": 2}

    assert sorted((await client.get(f"/api/conversations/{c1}/tags")).json()["tags"]) == ["idea", "wip"]
    assert (await client.get(f"/api/conversations/{c2}/tags")).json()["tags"] == ["wip"]
    assert not any(t["tag"] == "draft" for t in (await client.get("/api/tags")).json())


async def test_rename_merges_into_existing_tag_no_duplicate(client):
    await _signup(client)
    # this conversation already has BOTH the old and the new tag
    cid = await _conv_with_tags(client, ["old", "new"])

    r = await client.post("/api/tags/rename", json={"old": "old", "new": "new"})
    assert r.status_code == 200 and r.json()["count"] == 1  # one conversation, merged

    # the composite PK means the merge can't create a duplicate
    assert (await client.get(f"/api/conversations/{cid}/tags")).json()["tags"] == ["new"]


async def test_delete_tag_everywhere(client):
    await _signup(client)
    c1 = await _conv_with_tags(client, ["keep", "drop"])
    c2 = await _conv_with_tags(client, ["drop"])

    assert (await client.delete("/api/tags/drop")).status_code == 204
    assert (await client.get(f"/api/conversations/{c1}/tags")).json()["tags"] == ["keep"]
    assert (await client.get(f"/api/conversations/{c2}/tags")).json()["tags"] == []
    assert not any(t["tag"] == "drop" for t in (await client.get("/api/tags")).json())


async def test_tags_are_user_scoped(client):
    await _signup(client)  # alice
    await _conv_with_tags(client, ["alice-tag"])
    await client.post(
        "/api/admin/users", json={"username": "bob", "password": "passpass", "role": "user"}
    )
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": "bob", "password": "passpass"})

    # bob sees none of alice's tags, and renaming his (nonexistent) tag is a no-op
    assert (await client.get("/api/tags")).json() == []
    r = await client.post("/api/tags/rename", json={"old": "alice-tag", "new": "x"})
    assert r.status_code == 200 and r.json()["count"] == 0
    # alice's tag is untouched
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": "alice", "password": "hunter22hunter"})
    assert any(t["tag"] == "alice-tag" for t in (await client.get("/api/tags")).json())


async def test_tags_require_auth(client):
    assert (await client.get("/api/tags")).status_code == 401
