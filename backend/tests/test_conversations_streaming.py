"""Hard paths in the streaming engine (conversations._stream_and_persist) that
the old always-200 upstream fixture could not reach: upstream errors, the tool
loop bound, intermediate-[DONE] swallowing, empty completions, and malformed
tool-call arguments. Driven via the programmable `upstream` fixture."""
import json

from tests.conftest import content_chunk, error_response, finish, sse, tool_call_chunk


async def _signup(client):
    await client.post(
        "/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"}
    )


async def _new_conv(client, **patch):
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    if patch:
        await client.patch(f"/api/conversations/{cid}", json=patch)
    return cid


async def _raw(client, method, path, body=None):
    out = b""
    async with client.stream(method, path, json=body or {}) as r:
        assert r.status_code == 200, await r.aread()
        async for chunk in r.aiter_bytes():
            out += chunk
    return out.decode()


def _frames(text):
    """Parse an SSE body into a list of (event, data) tuples."""
    items = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        ev = data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        items.append((ev, data))
    return items


async def test_stream_surfaces_upstream_error_frame(client, upstream):
    await _signup(client)
    cid = await _new_conv(client)
    upstream.queue_chat(error_response(500, "kaboom"))

    text = await _raw(client, "POST", f"/api/conversations/{cid}/messages", {"content": "hi"})
    frames = _frames(text)
    error_frames = [d for _ev, d in frames if d and d != "[DONE]" and '"error"' in d]
    assert len(error_frames) == 1
    assert "kaboom" in json.loads(error_frames[0])["error"]
    assert "[DONE]" in [d for _ev, d in frames]

    # An upstream error must not persist a phantom assistant message.
    msgs = (await client.get(f"/api/conversations/{cid}")).json()["messages"]
    assert [m["role"] for m in msgs] == ["user"]


async def test_tool_loop_respects_max_iterations(client, upstream):
    await _signup(client)
    cid = await _new_conv(client, tools_enabled=True)

    # The upstream asks for the same tool on EVERY call — the loop must cap.
    upstream.set_chat(
        lambda body: sse(
            tool_call_chunk("calculate", '{"expression":"1+1"}'), finish("tool_calls")
        )
    )
    text = await _raw(client, "POST", f"/api/conversations/{cid}/messages", {"content": "go"})

    assert len(upstream.chat_calls) == 5  # max_tool_loops, not infinite
    assert text.count("data: [DONE]") == 1  # exactly one terminal DONE
    assert text.count("event: tool_call") == 5


async def test_intermediate_done_swallowed_single_done(client, upstream):
    await _signup(client)
    cid = await _new_conv(client, tools_enabled=True)
    upstream.queue_chat(
        sse(tool_call_chunk("calculate", '{"expression":"2+2"}'), finish("tool_calls")),
        sse(content_chunk("all done"), finish("stop")),
    )

    text = await _raw(client, "POST", f"/api/conversations/{cid}/messages", {"content": "x"})
    # Each upstream stream ended with its own [DONE]; the client sees only one.
    assert text.count("data: [DONE]") == 1
    assert "all done" in text


async def test_empty_completion_not_persisted(client, upstream):
    await _signup(client)
    cid = await _new_conv(client)
    upstream.queue_chat(sse(finish("stop")))  # finish only, no content

    await _raw(client, "POST", f"/api/conversations/{cid}/messages", {"content": "hello"})
    msgs = (await client.get(f"/api/conversations/{cid}")).json()["messages"]
    assert [m["role"] for m in msgs] == ["user"]  # no assistant row


async def test_malformed_tool_args_fall_back_to_empty(client, upstream):
    await _signup(client)
    cid = await _new_conv(client, tools_enabled=True)
    upstream.queue_chat(
        sse(tool_call_chunk("calculate", "{not valid json"), finish("tool_calls")),
        sse(content_chunk("recovered"), finish("stop")),
    )

    text = await _raw(client, "POST", f"/api/conversations/{cid}/messages", {"content": "x"})
    tool_events = [json.loads(d) for ev, d in _frames(text) if ev == "tool_call"]
    assert len(tool_events) == 1
    assert tool_events[0]["arguments"] == {}  # invalid JSON -> {}
    assert tool_events[0]["result"].startswith("error:")  # calculate w/o expression
    assert "recovered" in text

    msgs = (await client.get(f"/api/conversations/{cid}")).json()["messages"]
    assert msgs[-1]["role"] == "assistant" and msgs[-1]["content"] == "recovered"
