"""The public /v1 OpenAI-compatible surface: validation, error envelopes/frames,
and the embeddings endpoint."""
import httpx

from tests.conftest import error_response


async def _key(client) -> str:
    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})
    return (await client.post("/api/api_keys", json={"name": "k"})).json()["key"]


def _auth(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


async def test_v1_bearer_rejections(client):
    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})
    assert (await client.get("/v1/models")).status_code == 401
    assert (await client.get("/v1/models", headers={"Authorization": "Token x"})).status_code == 401
    assert (await client.get("/v1/models", headers={"Authorization": "Bearer "})).status_code == 401
    assert (await client.get("/v1/models", headers={"Authorization": "Bearer nope"})).status_code == 401


async def test_v1_chat_validation_400(client):
    key = await _key(client)
    # missing messages
    r = await client.post("/v1/chat/completions", headers=_auth(key), json={"model": "fake-a"})
    assert r.status_code == 400
    assert r.json()["error"]["message"] == "missing or invalid 'messages'"
    # missing model
    r = await client.post(
        "/v1/chat/completions", headers=_auth(key),
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 400
    assert "model" in r.json()["error"]["message"]


async def test_v1_nonstream_success_and_error(client, upstream):
    key = await _key(client)
    completion = {"id": "x", "object": "chat.completion", "choices": [{"message": {"content": "hi"}}]}
    upstream.queue_chat(httpx.Response(200, json=completion))
    r = await client.post(
        "/v1/chat/completions", headers=_auth(key),
        json={"model": "fake-a", "messages": [{"role": "user", "content": "hi"}], "stream": False},
    )
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "hi"

    upstream.queue_chat(error_response(500, "boom"))
    r = await client.post(
        "/v1/chat/completions", headers=_auth(key),
        json={"model": "fake-a", "messages": [{"role": "user", "content": "hi"}], "stream": False},
    )
    assert r.status_code == 502
    assert r.json()["error"]["type"] == "upstream_error"


async def test_v1_4xx_passthrough(client, upstream):
    """An upstream client error (e.g. 404 no-such-model) keeps its status + body
    so SDKs see the real error, instead of being collapsed to 502."""
    key = await _key(client)
    upstream.queue_chat(
        httpx.Response(404, json={"error": {"message": "no such model", "type": "not_found"}})
    )
    r = await client.post(
        "/v1/chat/completions", headers=_auth(key),
        json={"model": "fake-a", "messages": [{"role": "user", "content": "hi"}], "stream": False},
    )
    assert r.status_code == 404
    assert r.json()["error"]["message"] == "no such model"


async def test_v1_stream_error_frame(client, upstream):
    key = await _key(client)
    upstream.queue_chat(error_response(500, "boom"))
    body = b""
    async with client.stream(
        "POST", "/v1/chat/completions", headers=_auth(key),
        json={"model": "fake-a", "messages": [{"role": "user", "content": "hi"}], "stream": True},
    ) as r:
        assert r.status_code == 200
        async for chunk in r.aiter_bytes():
            body += chunk
    text = body.decode()
    assert '"error"' in text  # an OpenAI-shaped error frame
    assert "[DONE]" in text   # ...followed by the stream terminator


async def test_v1_embeddings(client):
    key = await _key(client)
    r = await client.post(
        "/v1/embeddings", headers=_auth(key), json={"model": "nomic-embed-text", "input": ["a", "b"]}
    )
    assert r.status_code == 200
    assert len(r.json()["data"]) == 2

    # validation
    r = await client.post("/v1/embeddings", headers=_auth(key), json={"model": "x"})
    assert r.status_code == 400
    assert "input" in r.json()["error"]["message"]
