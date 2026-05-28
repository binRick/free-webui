import json


async def _signup(client):
    await client.post(
        "/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"}
    )


async def _consume(client, path, body):
    async with client.stream("POST", path, json=body) as r:
        async for _ in r.aiter_lines():
            pass


async def test_memory_crud(client):
    await _signup(client)
    assert (await client.get("/api/memories")).json() == []
    m = (
        await client.post("/api/memories", json={"content": "I prefer concise replies."})
    ).json()
    listed = (await client.get("/api/memories")).json()
    assert [r["content"] for r in listed] == ["I prefer concise replies."]
    assert (await client.delete(f"/api/memories/{m['id']}")).status_code == 204
    assert (await client.get("/api/memories")).json() == []


async def test_memories_inject_into_send(client):
    """Every send should prepend the user's memories as a system message."""
    import httpx
    from app.main import app

    await _signup(client)
    await client.post(
        "/api/memories", json={"content": "Always answer in haiku."}
    )

    captured: dict = {}

    async def capture(request: httpx.Request) -> httpx.Response:
        from tests.conftest import _fake_chat_stream, _fake_handler
        if request.url.path.endswith("/chat/completions"):
            captured["payload"] = json.loads(request.content)
            return _fake_chat_stream(captured["payload"])
        return await _fake_handler(request)

    app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(capture), base_url="http://upstream/v1"
    )

    cid = (await client.post("/api/conversations", json={})).json()["id"]
    await _consume(client, f"/api/conversations/{cid}/messages", {"content": "ok"})

    systems = "\n".join(
        m["content"]
        for m in captured["payload"]["messages"]
        if m["role"] == "system"
    )
    assert "Always answer in haiku." in systems
