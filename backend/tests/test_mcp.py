"""MCP server CRUD + the dispatch loop that calls an MCP server's tools/call."""
import json


async def _signup(client):
    await client.post(
        "/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"}
    )


async def test_mcp_server_crud(client):
    await _signup(client)
    assert (await client.get("/api/mcp_servers")).json() == []

    created = (
        await client.post(
            "/api/mcp_servers",
            json={
                "name": "weather",
                "url": "http://mcp.local/jsonrpc",
                "headers": {"x-token": "abc"},
            },
        )
    ).json()
    sid = created["id"]
    assert created["enabled"] is True
    assert created["headers"] == {"x-token": "abc"}

    listed = (await client.get("/api/mcp_servers")).json()
    assert len(listed) == 1 and listed[0]["id"] == sid

    patched = await client.patch(
        f"/api/mcp_servers/{sid}", json={"enabled": False}
    )
    assert patched.json()["enabled"] is False

    assert (await client.delete(f"/api/mcp_servers/{sid}")).status_code == 204
    assert (await client.get("/api/mcp_servers")).json() == []


async def test_mcp_tool_call_loop(client, monkeypatch):
    """A conversation with an enabled MCP server should:
       1. compose tool specs from both built-in and MCP (tools/list),
       2. dispatch the LLM's tool call to the MCP server via tools/call,
       3. feed the result back into the next upstream message,
       4. stream the final assistant content.
    """
    import httpx
    from app.main import app

    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]

    server = (
        await client.post(
            "/api/mcp_servers",
            json={"name": "weather", "url": "http://mcp.test/jsonrpc"},
        )
    ).json()
    sid = server["id"]

    await client.patch(
        f"/api/conversations/{cid}", json={"tools_enabled": True}
    )

    # Track ALL upstream chat-completions payloads to assert composition + handoff.
    seen_payloads: list[dict] = []
    mcp_calls: list[dict] = []
    mcp_initialized = {"v": False}

    def _stream(chunks: list[dict]) -> httpx.Response:
        body = b""
        for c in chunks:
            body += f"data: {json.dumps(c)}\n\n".encode()
        body += b"data: [DONE]\n\n"
        return httpx.Response(
            200, content=body, headers={"content-type": "text/event-stream"}
        )

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        from tests.conftest import _fake_handler
        if not request.url.path.endswith("/chat/completions"):
            return await _fake_handler(request)
        payload = json.loads(request.content)
        seen_payloads.append(payload)
        # First call: tool registry must include MCP-namespaced tool;
        # respond with a tool_call to that MCP tool.
        if len(seen_payloads) == 1:
            return _stream([
                {
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": "call_a",
                                "type": "function",
                                "function": {
                                    "name": f"mcp_{sid}_get_weather",
                                    "arguments": '{"city":"Brooklyn"}',
                                },
                            }]
                        }
                    }]
                },
                {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            ])
        # Second call: now has the tool message; produce final content
        return _stream([
            {"choices": [{"delta": {"content": "Brooklyn is 18°C and clear."}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ])

    # MCP server handler: handles both tools/list (during composition) and
    # tools/call (during the loop). Direct httpx.AsyncClient() use in mcp.py
    # has to be intercepted via monkeypatch.
    def mcp_handler(request: httpx.Request) -> httpx.Response:
        if not request.url.host == "mcp.test":
            # fall through to upstream
            return httpx.Response(404)
        body = json.loads(request.content)
        method = body.get("method")
        if method == "tools/list":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body.get("id"),
                    "result": {
                        "tools": [
                            {
                                "name": "get_weather",
                                "description": "Get current weather for a city.",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "city": {"type": "string"}
                                    },
                                    "required": ["city"],
                                },
                            }
                        ]
                    },
                },
            )
        if method == "tools/call":
            mcp_calls.append(body["params"])
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body.get("id"),
                    "result": {
                        "content": [
                            {"type": "text", "text": "18°C, clear"}
                        ]
                    },
                },
            )
        return httpx.Response(400, json={"error": {"message": "unknown method"}})

    # Combined transport: routes by hostname.
    async def combined(request: httpx.Request) -> httpx.Response:
        if request.url.host == "mcp.test":
            return mcp_handler(request)
        return await upstream_handler(request)

    app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(combined), base_url="http://upstream/v1"
    )
    # mcp.py builds its own httpx.AsyncClient() per call; monkeypatch the
    # class so those use the mock transport too.
    OriginalAsyncClient = httpx.AsyncClient

    class PatchedAsyncClient(OriginalAsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("transport", httpx.MockTransport(combined))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedAsyncClient)

    tool_events: list[dict] = []
    deltas: list[str] = []
    async with client.stream(
        "POST", f"/api/conversations/{cid}/messages", json={"content": "weather?"}
    ) as r:
        text = ""
        async for chunk in r.aiter_bytes():
            text += chunk.decode()

    for frame in text.split("\n\n"):
        frame = frame.strip()
        if not frame:
            continue
        kind = None
        for line in frame.splitlines():
            if line.startswith("event:"):
                kind = line[6:].strip()
            elif line.startswith("data:"):
                d = line[5:].strip()
                if d == "[DONE]":
                    continue
                obj = json.loads(d)
                if kind == "tool_call":
                    tool_events.append(obj)
                else:
                    c = obj.get("choices", [{}])[0].get("delta", {}).get("content")
                    if isinstance(c, str):
                        deltas.append(c)

    # Tools list call was made on MCP server
    assert mcp_calls == [{"name": "get_weather", "arguments": {"city": "Brooklyn"}}]
    # And the MCP-namespaced tool was offered to the LLM
    first_tools = seen_payloads[0]["tools"]
    names = [t["function"]["name"] for t in first_tools]
    assert f"mcp_{sid}_get_weather" in names
    assert "calculate" in names  # built-ins still present
    # And the tool event was surfaced + the final reply persisted
    assert tool_events and tool_events[0]["result"] == "18°C, clear"
    assert "".join(deltas) == "Brooklyn is 18°C and clear."
    conv = (await client.get(f"/api/conversations/{cid}")).json()
    assert conv["messages"][-1]["content"] == "Brooklyn is 18°C and clear."
    _ = mcp_initialized  # currently unused
