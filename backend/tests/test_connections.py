"""Multiple upstream connections: admin CRUD, the probe endpoint, and per-model
routing across connections."""
import json

import httpx


async def _admin(client):
    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})


def _models_resp(ids):
    return httpx.Response(200, json={"data": [{"id": i} for i in ids]})


def _chat_resp(text):
    body = b"".join(
        f"data: {json.dumps({'choices': [{'delta': {'content': tok + ' '}}]})}\n\n".encode()
        for tok in text.split(" ")
    )
    body += b"data: [DONE]\n\n"
    return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})


def _multihost_transport():
    """Route by host: conn2.test serves 'special-model', everything else (the env
    upstream at localhost) serves fake-a/fake-b."""
    async def handler(req: httpx.Request) -> httpx.Response:
        host, path = req.url.host, req.url.path
        if path.endswith("/models"):
            if host == "fail.test":
                return httpx.Response(500, text="boom")
            return _models_resp(["special-model"]) if host == "conn2.test" else _models_resp(["fake-a", "fake-b"])
        if path.endswith("/chat/completions"):
            return _chat_resp("from conn two") if host == "conn2.test" else _chat_resp("from default")
        return httpx.Response(404)
    return httpx.MockTransport(handler)


async def _stream_text(client, path, body):
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
            delta = json.loads(data).get("choices", [{}])[0].get("delta", {}).get("content")
            if isinstance(delta, str):
                text += delta
    return text


async def test_connection_crud_and_key_is_write_only(client):
    await _admin(client)
    c = (
        await client.post(
            "/api/admin/connections",
            json={"name": "vllm", "base_url": "http://vllm.internal:8000/v1", "api_key": "secret"},
        )
    ).json()
    assert c["has_api_key"] is True
    assert "api_key" not in c  # the secret is never returned

    listing = (await client.get("/api/admin/connections")).json()
    assert len(listing) == 1 and listing[0]["base_url"] == "http://vllm.internal:8000/v1"

    p = (await client.patch(f"/api/admin/connections/{c['id']}", json={"enabled": False})).json()
    assert p["enabled"] is False
    assert p["has_api_key"] is True  # omitting api_key keeps the existing one

    assert (await client.delete(f"/api/admin/connections/{c['id']}")).status_code == 204
    assert (await client.get("/api/admin/connections")).json() == []


async def test_connections_require_admin(client):
    await _admin(client)
    await client.post("/api/admin/users", json={"username": "bob", "password": "passpass", "role": "user"})
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": "bob", "password": "passpass"})
    assert (await client.get("/api/admin/connections")).status_code == 403


async def test_model_routing_across_connections(client):
    from app.main import app

    await _admin(client)
    app.state.http = httpx.AsyncClient(
        transport=_multihost_transport(), base_url="http://localhost:11434/v1"
    )
    await client.post("/api/admin/connections", json={"name": "two", "base_url": "http://conn2.test/v1"})

    # /api/models merges across the env connection + the new one
    models = {m["id"] for m in (await client.get("/api/models")).json()["data"]}
    assert models == {"fake-a", "fake-b", "special-model"}

    # a model only served by conn2 routes there
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    out = await _stream_text(client, f"/api/conversations/{cid}/messages", {"content": "hi", "model": "special-model"})
    assert "from conn two" in out

    # a model served by the env upstream routes to it
    cid2 = (await client.post("/api/conversations", json={})).json()["id"]
    out2 = await _stream_text(client, f"/api/conversations/{cid2}/messages", {"content": "hi", "model": "fake-a"})
    assert "from default" in out2


async def test_keyless_connection_does_not_leak_default_key(client):
    from app.main import app

    await _admin(client)
    seen: dict[str, str | None] = {}

    async def handler(req: httpx.Request) -> httpx.Response:
        host, path = req.url.host, req.url.path
        if path.endswith("/models"):
            return _models_resp(["special-model"]) if host == "conn2.test" else _models_resp(["fake-a"])
        if path.endswith("/chat/completions"):
            seen[host] = req.headers.get("authorization")
            return _chat_resp("ok")
        return httpx.Response(404)

    app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://localhost:11434/v1"
    )
    # a keyless extra connection
    await client.post("/api/admin/connections", json={"name": "two", "base_url": "http://conn2.test/v1"})
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    await _stream_text(client, f"/api/conversations/{cid}/messages", {"content": "hi", "model": "special-model"})
    # the env upstream's key must NOT be forwarded to the keyless connection
    assert seen.get("conn2.test") in (None, "")


async def test_malformed_models_payload_does_not_crash(client):
    from app.main import app

    await _admin(client)

    async def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/models"):
            if req.url.host == "bad.test":
                return httpx.Response(200, json={"data": 123})  # non-list -> malformed
            return _models_resp(["fake-a"])
        return httpx.Response(404)

    app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://localhost:11434/v1"
    )
    await client.post("/api/admin/connections", json={"name": "bad", "base_url": "http://bad.test/v1"})

    r = await client.get("/api/models")  # must not 500
    assert r.status_code == 200
    assert [m["id"] for m in r.json()["data"]] == ["fake-a"]  # bad connection contributes nothing

    t = (await client.post("/api/admin/connections/test", json={"name": "b", "base_url": "http://bad.test/v1"})).json()
    assert t["ok"] is False  # probe reports the bad shape rather than 500ing


async def test_connection_test_endpoint(client):
    from app.main import app

    await _admin(client)
    app.state.http = httpx.AsyncClient(
        transport=_multihost_transport(), base_url="http://localhost:11434/v1"
    )
    ok = (await client.post("/api/admin/connections/test", json={"name": "t", "base_url": "http://conn2.test/v1"})).json()
    assert ok["ok"] is True and ok["models"] == ["special-model"]

    bad = (await client.post("/api/admin/connections/test", json={"name": "t", "base_url": "http://fail.test/v1"})).json()
    assert bad["ok"] is False and "500" in bad["error"]
