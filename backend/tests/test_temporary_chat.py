"""Temporary chat: stateless streaming completion that never persists."""
from tests.conftest import content_chunk, finish, sse


async def _signup(client):
    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})


async def _consume(client, path, body):
    text = ""
    async with client.stream("POST", path, json=body) as r:
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
            delta = (payload.get("choices") or [{}])[0].get("delta", {}).get("content")
            if isinstance(delta, str):
                text += delta
    return text


async def test_temporary_chat_streams_without_persisting(client, upstream):
    await _signup(client)
    upstream.queue_chat(sse(content_chunk("ephemeral reply"), finish("stop")))
    text = await _consume(
        client,
        "/api/chat/temporary",
        {"messages": [{"role": "user", "content": "hi"}]},
    )
    assert text == "ephemeral reply"

    # nothing was written: no conversations, no messages
    assert (await client.get("/api/conversations")).json() == []
    from app.main import app

    assert (await (await app.state.db.execute("SELECT COUNT(*) FROM conversations")).fetchone())[0] == 0
    assert (await (await app.state.db.execute("SELECT COUNT(*) FROM messages")).fetchone())[0] == 0


async def test_temporary_chat_replays_full_transcript(client, upstream):
    await _signup(client)
    upstream.queue_chat(sse(content_chunk("ok"), finish("stop")))
    await _consume(
        client,
        "/api/chat/temporary",
        {
            "system_prompt": "be terse",
            "messages": [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "second"},
            ],
        },
    )
    sent = upstream.chat_calls[-1]["messages"]
    assert sent[0] == {"role": "system", "content": "be terse"}
    assert [m["role"] for m in sent[1:]] == ["user", "assistant", "user"]
    assert sent[-1]["content"] == "second"


async def test_temporary_chat_requires_auth(client):
    r = await client.post("/api/chat/temporary", json={"messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 401


async def test_temporary_chat_validates(client):
    await _signup(client)
    # empty transcript
    assert (await client.post("/api/chat/temporary", json={"messages": []})).status_code == 422
    # bad role
    r = await client.post(
        "/api/chat/temporary", json={"messages": [{"role": "robot", "content": "x"}]}
    )
    assert r.status_code == 422


async def test_temporary_chat_forwards_vision_image(client, upstream):
    """A user turn with a data: image part (call-mode vision) reaches upstream
    intact as multimodal content."""
    await _signup(client)
    upstream.queue_chat(sse(content_chunk("i see it"), finish("stop")))
    data_url = "data:image/jpeg;base64,/9j/4AAQSkZJR0=="
    await _consume(
        client,
        "/api/chat/temporary",
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "what is this?"},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
        },
    )
    sent = upstream.chat_calls[-1]["messages"][-1]
    assert sent["role"] == "user" and isinstance(sent["content"], list)
    kinds = [p["type"] for p in sent["content"]]
    assert kinds == ["text", "image_url"]
    assert sent["content"][1]["image_url"]["url"] == data_url


async def test_temporary_chat_rejects_remote_image_url(client):
    """Only data: image URLs are allowed — a remote URL must not turn the
    upstream into an SSRF fetch agent."""
    await _signup(client)
    r = await client.post(
        "/api/chat/temporary",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": "http://169.254.169.254/latest"}}
                    ],
                }
            ]
        },
    )
    assert r.status_code == 422


async def test_temporary_chat_enforces_model_access(client, upstream):
    await _signup(client)  # alice is admin
    me = (await client.get("/api/auth/me")).json()
    # restrict "locked" to alice only; bob (a normal user) is excluded
    await client.put("/api/admin/model_access", json={"model_id": "locked", "user_ids": [me["id"]]})
    await client.post(
        "/api/admin/users", json={"username": "bob", "password": "passpass", "role": "user"}
    )
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": "bob", "password": "passpass"})
    r = await client.post(
        "/api/chat/temporary",
        json={"model": "locked", "messages": [{"role": "user", "content": "x"}]},
    )
    assert r.status_code == 403
