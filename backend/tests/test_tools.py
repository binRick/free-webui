"""Tests for the built-in tool registry and the streaming tool loop."""
import json


def test_calculator_safe_eval():
    from app.tools import run_tool
    assert run_tool("calculate", {"expression": "(1 + 2) * 3"}) == "9"
    assert run_tool("calculate", {"expression": "2**8"}) == "256"
    # Rejects names / arbitrary calls
    assert run_tool("calculate", {"expression": "__import__('os')"}) .startswith("error:")
    assert run_tool("calculate", {"expression": "print('x')"}).startswith("error:")
    # Division by zero
    assert run_tool("calculate", {"expression": "1/0"}).startswith("error:")


def test_calculate_rejects_exponent_dos():
    """A huge exponent must be refused promptly, not hang the worker on a
    multi-gigabyte big-int (e.g. 10**100000000)."""
    from app.tools import run_tool

    assert run_tool("calculate", {"expression": "10**100000000"}).startswith("error:")
    # Nested powers can't be used to sneak past the exponent cap either.
    assert run_tool("calculate", {"expression": "(10**1000)**1000"}).startswith("error:")
    # Ordinary powers still evaluate.
    assert run_tool("calculate", {"expression": "2**10"}) == "1024"
    assert run_tool("calculate", {"expression": "2**8"}) == "256"


def test_now_returns_iso_string():
    from app.tools import run_tool
    s = run_tool("now", {})
    # ISO-ish: YYYY-MM-DD...
    assert s[0].isdigit() and "-" in s and "T" in s


async def _signup(client):
    await client.post(
        "/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"}
    )


async def test_tool_loop_executes_calculator(client):
    """When tools_enabled is on and the upstream emits a tool_call, the
    server must execute the tool, surface a tool_call SSE event to the
    client, and continue streaming until the upstream finishes normally."""
    import httpx
    from app.main import app

    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    await client.patch(
        f"/api/conversations/{cid}", json={"tools_enabled": True}
    )

    call_count = {"n": 0}

    def _stream_chunks(chunks: list[dict]) -> httpx.Response:
        body = b""
        for c in chunks:
            body += f"data: {json.dumps(c)}\n\n".encode()
        body += b"data: [DONE]\n\n"
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    async def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/chat/completions"):
            from tests.conftest import _fake_handler
            return await _fake_handler(request)

        call_count["n"] += 1
        body = json.loads(request.content)
        msgs = body["messages"]
        # First call: ask the calculator for 2+3
        if call_count["n"] == 1:
            return _stream_chunks([
                {
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "calculate",
                                    "arguments": "{\"expression\": \"2+3\"}",
                                },
                            }]
                        }
                    }]
                },
                {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            ])
        # Second call: the model should now have the tool result and reply
        last = msgs[-1]
        assert last["role"] == "tool"
        assert last["content"] == "5"
        return _stream_chunks([
            {"choices": [{"delta": {"content": "the answer is "}}]},
            {"choices": [{"delta": {"content": last["content"]}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ])

    app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://upstream/v1"
    )

    deltas = []
    tool_events = []
    async with client.stream(
        "POST", f"/api/conversations/{cid}/messages", json={"content": "what is 2+3?"}
    ) as r:
        # Read raw stream so we can see custom `event: tool_call` frames
        raw = b""
        async for chunk in r.aiter_bytes():
            raw += chunk
        text = raw.decode()

    # Parse the SSE stream
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
                payload = json.loads(d)
                if kind == "tool_call":
                    tool_events.append(payload)
                else:
                    content = payload.get("choices", [{}])[0].get("delta", {}).get("content")
                    if isinstance(content, str):
                        deltas.append(content)

    assert tool_events == [
        {"name": "calculate", "arguments": {"expression": "2+3"}, "result": "5"}
    ]
    assert "".join(deltas) == "the answer is 5"
    assert call_count["n"] == 2

    # Persisted final assistant message:
    conv = (await client.get(f"/api/conversations/{cid}")).json()
    asst = [m for m in conv["messages"] if m["role"] == "assistant"]
    assert asst and asst[-1]["content"] == "the answer is 5"
