"""Anthropic-compatible /anthropic/v1/messages proxy: auth, translation,
streaming, and access control."""
import json

import httpx

from tests.conftest import content_chunk, finish, sse


async def _key(client) -> str:
    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})
    return (await client.post("/api/api_keys", json={"name": "k"})).json()["key"]


def _body(**over):
    b = {"model": "fake-a", "max_tokens": 64, "messages": [{"role": "user", "content": "hi"}]}
    b.update(over)
    return b


async def test_anthropic_requires_key(client):
    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})
    r = await client.post("/anthropic/v1/messages", json=_body())
    assert r.status_code == 401


async def test_anthropic_non_stream_translation(client, upstream):
    key = await _key(client)
    completion = {
        "choices": [{"message": {"content": "hello there"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
    }
    upstream.queue_chat(httpx.Response(200, json=completion))

    r = await client.post(
        "/anthropic/v1/messages", headers={"x-api-key": key},
        json=_body(system="be terse", messages=[{"role": "user", "content": "hi"}]),
    )
    assert r.status_code == 200
    a = r.json()
    assert a["type"] == "message" and a["role"] == "assistant"
    assert a["content"] == [{"type": "text", "text": "hello there"}]
    assert a["stop_reason"] == "end_turn"
    assert a["usage"] == {"input_tokens": 3, "output_tokens": 2}

    # the upstream got an OpenAI body with the system prepended + max_tokens
    sent = upstream.chat_calls[-1]
    assert sent["messages"][0] == {"role": "system", "content": "be terse"}
    assert sent["max_tokens"] == 64


async def test_anthropic_streaming_events(client, upstream):
    key = await _key(client)
    upstream.queue_chat(sse(content_chunk("Hel"), content_chunk("lo"), finish("stop")))

    raw = b""
    async with client.stream(
        "POST", "/anthropic/v1/messages", headers={"x-api-key": key}, json=_body(stream=True)
    ) as r:
        assert r.status_code == 200
        async for chunk in r.aiter_bytes():
            raw += chunk
    text = raw.decode()
    # the Anthropic event sequence
    for ev in ("message_start", "content_block_start", "content_block_delta", "content_block_stop", "message_stop"):
        assert f"event: {ev}" in text
    # the streamed text deltas
    deltas = [
        json.loads(line[6:])["delta"]["text"]
        for block in text.split("\n\n")
        for line in block.splitlines()
        if line.startswith("data:") and '"text_delta"' in line
    ]
    assert "".join(deltas) == "Hello"


async def test_anthropic_validation(client):
    key = await _key(client)
    r = await client.post("/anthropic/v1/messages", headers={"x-api-key": key}, json={"model": "fake-a", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 400  # missing max_tokens
    assert r.json()["type"] == "error"


async def test_anthropic_model_access_enforced(client):
    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})
    me = (await client.get("/api/auth/me")).json()
    await client.post("/api/admin/users", json={"username": "bob", "password": "passpass", "role": "user"})
    # restrict fake-a to alice only -> bob can't use it
    await client.put("/api/admin/model_access", json={"model_id": "fake-a", "user_ids": [me["id"]]})

    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": "bob", "password": "passpass"})
    key = (await client.post("/api/api_keys", json={"name": "k"})).json()["key"]

    r = await client.post("/anthropic/v1/messages", headers={"x-api-key": key}, json=_body())
    assert r.status_code == 403
    assert r.json()["error"]["type"] == "permission_error"
