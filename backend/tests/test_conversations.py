async def _signup(client, username="alice", password="hunter22"):
    r = await client.post(
        "/api/auth/setup", json={"username": username, "password": password}
    )
    return r


async def _consume_stream(client, method, path, json_body=None):
    """POST/PATCH the streaming endpoint and return the assembled text."""
    text = ""
    async with client.stream(method, path, json=json_body or {}) as r:
        assert r.status_code == 200, await r.aread()
        async for line in r.aiter_lines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            import json as _json
            payload = _json.loads(data)
            delta = payload.get("choices", [{}])[0].get("delta", {}).get("content")
            if isinstance(delta, str):
                text += delta
    return text


async def test_full_chat_lifecycle(client):
    await _signup(client)

    # Create empty conv — should not appear in list yet.
    r = await client.post("/api/conversations", json={"model": None})
    assert r.status_code == 200
    cid = r.json()["id"]

    listed = await client.get("/api/conversations")
    assert listed.status_code == 200
    assert listed.json() == []  # empty conv is hidden

    # Send first message — auto-titles + persists.
    text = await _consume_stream(
        client,
        "POST",
        f"/api/conversations/{cid}/messages",
        {"content": "hello"},
    )
    assert text.strip() == "echo: hello"

    listed = await client.get("/api/conversations")
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["title"] == "hello"

    # Persisted both turns.
    full = await client.get(f"/api/conversations/{cid}")
    assert full.status_code == 200
    msgs = full.json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["content"].strip() == "echo: hello"

    # Regenerate drops the last assistant + re-streams.
    text2 = await _consume_stream(
        client, "POST", f"/api/conversations/{cid}/regenerate"
    )
    assert text2.strip() == "echo: hello"

    full2 = await client.get(f"/api/conversations/{cid}")
    msgs2 = full2.json()["messages"]
    # Still 2 turns (regenerate replaced the old assistant).
    assert [m["role"] for m in msgs2] == ["user", "assistant"]


async def test_edit_message_truncates_and_restreams(client):
    await _signup(client)
    r = await client.post("/api/conversations", json={"model": None})
    cid = r.json()["id"]
    await _consume_stream(
        client, "POST", f"/api/conversations/{cid}/messages", {"content": "first"}
    )
    await _consume_stream(
        client, "POST", f"/api/conversations/{cid}/messages", {"content": "second"}
    )

    full = await client.get(f"/api/conversations/{cid}")
    msgs = full.json()["messages"]
    assert len(msgs) == 4
    first_user_id = msgs[0]["id"]

    text = await _consume_stream(
        client,
        "PATCH",
        f"/api/conversations/{cid}/messages/{first_user_id}",
        {"content": "rewritten"},
    )
    assert text.strip() == "echo: rewritten"

    full2 = await client.get(f"/api/conversations/{cid}")
    msgs2 = full2.json()["messages"]
    # Everything after the edited user message was truncated, then one
    # fresh assistant message was streamed.
    assert len(msgs2) == 2
    assert msgs2[0]["content"] == "rewritten"
    assert msgs2[1]["content"].strip() == "echo: rewritten"


async def test_update_conversation_params(client):
    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    patch = await client.patch(
        f"/api/conversations/{cid}",
        json={
            "system_prompt": "be terse",
            "temperature": 0.4,
            "top_p": 0.95,
            "stop": ["###", "END"],
        },
    )
    assert patch.status_code == 200
    body = patch.json()
    assert body["system_prompt"] == "be terse"
    assert body["temperature"] == 0.4
    assert body["top_p"] == 0.95
    assert body["stop"] == ["###", "END"]


async def test_multimodal_message_persists_and_streams(client):
    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    content = [
        {"type": "text", "text": "describe this image"},
        {
            "type": "image_url",
            "image_url": {
                "url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQABh6FO1AAAAABJRU5ErkJggg=="
            },
        },
    ]
    text = await _consume_stream(
        client,
        "POST",
        f"/api/conversations/{cid}/messages",
        {"content": content},
    )
    # Fake upstream echoes the text part only.
    assert text.strip() == "echo: describe this image"

    full = await client.get(f"/api/conversations/{cid}")
    msgs = full.json()["messages"]
    import json as _json
    parts = _json.loads(msgs[0]["content"])
    assert parts[0]["type"] == "text"
    assert parts[1]["type"] == "image_url"
    # Auto-title used the text part, not "[..."
    assert full.json()["title"] == "describe this image"


async def test_user_isolation(client):
    """Conversations are scoped to their owner; cross-user access 404s."""
    # alice creates a conversation
    await _signup(client, "alice", "passpass")
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    await _consume_stream(
        client, "POST", f"/api/conversations/{cid}/messages", {"content": "owned by alice"}
    )
    assert (await client.get(f"/api/conversations/{cid}")).status_code == 200

    # bob logs in (no /setup available — must be created via second user
    # path). Since /setup blocks after a user exists, we need an alt path:
    # for now, manually insert bob via the DB and log in.
    from app.auth import hash_password
    from app.config import settings
    import aiosqlite
    import time as _t

    async with aiosqlite.connect(settings.db_path) as raw:
        await raw.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
            ("bob", hash_password("passpass"), "user", int(_t.time())),
        )
        await raw.commit()

    await client.post("/api/auth/logout")
    await client.post(
        "/api/auth/login", json={"username": "bob", "password": "passpass"}
    )
    # bob can't see alice's chat
    assert (await client.get(f"/api/conversations/{cid}")).status_code == 404
    assert (await client.delete(f"/api/conversations/{cid}")).status_code == 404
    assert (await client.get("/api/conversations")).json() == []
