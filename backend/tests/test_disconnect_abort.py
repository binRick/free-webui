"""Client-disconnect abort in the streaming engine (conversations._stream_and_persist).

When the client goes away mid-stream the loop must stop pulling upstream tokens
and stop the tool loop — but still persist whatever was generated so far (the
frontend's stop button keeps the on-screen partial, so a reload must match).

Driven by calling the generator directly with an injected `is_disconnected`
probe, so the abort path is exercised without a real socket.
"""
from tests.conftest import content_chunk, finish, sse, tool_call_chunk


async def _signup(client):
    await client.post(
        "/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"}
    )


async def _new_conv(client):
    return (await client.post("/api/conversations", json={})).json()["id"]


def _flip_after(n: int):
    """An is_disconnected probe that reports 'connected' for the first n polls,
    then 'gone' forever after."""
    state = {"calls": 0}

    async def probe() -> bool:
        state["calls"] += 1
        return state["calls"] > n

    return probe


async def _drain(agen) -> bytes:
    out = b""
    async for chunk in agen:
        out += chunk
    return out


async def test_disconnect_stops_tool_loop_before_next_round(client, upstream):
    await _signup(client)
    cid = await _new_conv(client)
    from app.conversations import _stream_and_persist
    from app.main import app

    # Upstream asks for a tool on every call — without an abort this runs the
    # full 5-iteration loop (see test_tool_loop_respects_max_iterations).
    upstream.set_chat(
        lambda body: sse(
            tool_call_chunk("calculate", '{"expression":"1+1"}'), finish("tool_calls")
        )
    )
    frames = await _drain(
        _stream_and_persist(
            app.state.db, app.state.http, cid,
            [{"role": "user", "content": "go"}], "fake-a",
            None, None, None, tools_enabled=True, user_id=1,
            is_disconnected=_flip_after(1),  # gone right after the first stream chunk
        )
    )

    # Aborted inside the first round: exactly one upstream call (not 5), and the
    # requested tool was never executed (no tool_call event surfaced).
    assert len(upstream.chat_calls) == 1
    assert b"event: tool_call" not in frames
    assert frames.count(b"data: [DONE]") == 1


async def test_disconnect_persists_partial_reply(client, upstream):
    await _signup(client)
    cid = await _new_conv(client)
    from app.conversations import _stream_and_persist
    from app.main import app

    upstream.queue_chat(
        sse(
            content_chunk("alpha "), content_chunk("beta "), content_chunk("gamma "),
            finish("stop"),
        )
    )
    frames = await _drain(
        _stream_and_persist(
            app.state.db, app.state.http, cid,
            [{"role": "user", "content": "go"}], "fake-a",
            None, None, None, user_id=1,
            is_disconnected=_flip_after(1),  # gone after the first token
        )
    )

    # Only the first token made it onto the wire before the abort.
    assert b"alpha" in frames and b"gamma" not in frames

    # ...and the partial (not the full reply) is what got persisted, so a reload
    # shows exactly what the user saw when they disconnected.
    msgs = (await client.get(f"/api/conversations/{cid}")).json()["messages"]
    assert msgs[-1]["role"] == "assistant"
    assert msgs[-1]["content"].strip() == "alpha"


async def test_regenerate_abort_at_zero_tokens_restores_prior_reply(client, upstream):
    """Regenerate archives the prior reply (active=0) before streaming. If the
    client disconnects before the first token, persisting nothing would leave the
    turn with no active assistant message (and no UI way back). The prior reply
    must be restored instead — this also covers an upstream error mid-regenerate."""
    await _signup(client)
    cid = await _new_conv(client)
    from app.conversations import _stream_and_persist
    from app.main import app

    db = app.state.db
    # Produce a real first reply.
    await _drain(
        _stream_and_persist(
            db, app.state.http, cid, [{"role": "user", "content": "hi"}], "fake-a",
            None, None, None, user_id=1,
        )
    )
    msgs = (await client.get(f"/api/conversations/{cid}")).json()["messages"]
    assistant = [m for m in msgs if m["role"] == "assistant"][-1]
    archived_id, original = assistant["id"], assistant["content"]

    # Simulate the regenerate archive: prior reply goes active=0 before streaming.
    await db.execute("UPDATE messages SET active = 0 WHERE id = ?", (archived_id,))
    await db.commit()

    # Regenerate that aborts before the loop even opens the upstream stream.
    calls_before = len(upstream.chat_calls)
    await _drain(
        _stream_and_persist(
            db, app.state.http, cid, [{"role": "user", "content": "hi"}], "fake-a",
            None, None, None, user_id=1,
            parent_message_id=archived_id,
            is_disconnected=_flip_after(0),  # gone before the first poll succeeds
        )
    )

    # No upstream round happened for the regenerate, and the prior reply is
    # restored as the active trailing assistant — not lost, no empty turn.
    assert len(upstream.chat_calls) == calls_before
    msgs2 = (await client.get(f"/api/conversations/{cid}")).json()["messages"]
    assistants = [m for m in msgs2 if m["role"] == "assistant"]
    assert len(assistants) == 1
    assert assistants[0]["id"] == archived_id
    assert assistants[0]["content"] == original
    assert msgs2[-1]["role"] == "assistant"


async def test_no_probe_runs_to_completion(client, upstream):
    """Without an is_disconnected probe (or when it never fires) the stream runs
    to its natural end — the abort path must not change normal completion."""
    await _signup(client)
    cid = await _new_conv(client)
    from app.conversations import _stream_and_persist
    from app.main import app

    upstream.queue_chat(sse(content_chunk("full answer"), finish("stop")))
    frames = await _drain(
        _stream_and_persist(
            app.state.db, app.state.http, cid,
            [{"role": "user", "content": "go"}], "fake-a",
            None, None, None, user_id=1,
            is_disconnected=None,
        )
    )
    assert b"full answer" in frames
    assert frames.count(b"data: [DONE]") == 1
    msgs = (await client.get(f"/api/conversations/{cid}")).json()["messages"]
    assert msgs[-1]["content"] == "full answer"
