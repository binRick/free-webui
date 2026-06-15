"""Clone a conversation into a fresh one (settings + visible thread + tags +
collections), owned by the same user."""


async def _signup(client):
    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})


async def _consume(client, method, path, body=None):
    async with client.stream(method, path, json=body or {}) as r:
        assert r.status_code == 200, await r.aread()
        async for _ in r.aiter_bytes():
            pass


async def _new(client):
    return (await client.post("/api/conversations", json={})).json()["id"]


async def test_clone_copies_thread_settings_and_tags(client):
    await _signup(client)
    cid = await _new(client)
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", {"content": "hello"})
    await client.patch(
        f"/api/conversations/{cid}",
        json={"title": "Original", "system_prompt": "be terse", "temperature": 0.3},
    )
    await client.put(f"/api/conversations/{cid}/tags", json={"tags": ["work", "draft"]})

    r = await client.post(f"/api/conversations/{cid}/clone")
    assert r.status_code == 200
    summary = r.json()
    new_id = summary["id"]
    assert new_id != cid
    assert summary["title"] == "Original (copy)"
    assert sorted(summary["tags"]) == ["draft", "work"]

    # the clone is an independent conversation with the same visible thread...
    clone = (await client.get(f"/api/conversations/{new_id}")).json()
    assert [m["role"] for m in clone["messages"]] == ["user", "assistant"]
    assert clone["messages"][0]["content"] == "hello"
    assert clone["system_prompt"] == "be terse"
    assert clone["temperature"] == 0.3

    # ...and editing the clone does not touch the original.
    await client.patch(f"/api/conversations/{new_id}", json={"title": "Diverged"})
    assert (await client.get(f"/api/conversations/{cid}")).json()["title"] == "Original"


async def test_clone_only_copies_active_messages(client, upstream):
    from tests.conftest import content_chunk, finish, sse

    await _signup(client)
    cid = await _new(client)
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", {"content": "hi"})
    # regenerate -> archives the first reply as an inactive variant
    upstream.queue_chat(sse(content_chunk("second take"), finish("stop")))
    await _consume(client, "POST", f"/api/conversations/{cid}/regenerate")

    new_id = (await client.post(f"/api/conversations/{cid}/clone")).json()["id"]
    clone = (await client.get(f"/api/conversations/{new_id}")).json()
    # only the active reply is carried over, not the archived variant
    assert [m["role"] for m in clone["messages"]] == ["user", "assistant"]
    assert clone["messages"][1]["content"] == "second take"


async def test_clone_requires_ownership(client):
    await _signup(client)
    cid = await _new(client)
    await client.post(
        "/api/admin/users", json={"username": "bob", "password": "passpass", "role": "user"}
    )
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": "bob", "password": "passpass"})
    assert (await client.post(f"/api/conversations/{cid}/clone")).status_code == 404
