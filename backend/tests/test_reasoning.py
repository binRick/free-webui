"""Reasoning models: a separate `reasoning_content` stream is normalized into
inline <think>…</think> (persisted + streamed), and reasoning is stripped from
the assistant history replayed back upstream."""
from tests.conftest import content_chunk, finish, sse


async def _signup(client):
    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})


async def _new(client):
    return (await client.post("/api/conversations", json={})).json()["id"]


async def _consume(client, cid, content):
    raw = b""
    async with client.stream(
        "POST", f"/api/conversations/{cid}/messages", json={"content": content}
    ) as r:
        assert r.status_code == 200
        async for chunk in r.aiter_bytes():
            raw += chunk
    return raw.decode()


def _reasoning_chunk(text):
    return {"choices": [{"delta": {"reasoning_content": text}}]}


async def test_reasoning_content_normalized_to_think(client, upstream):
    await _signup(client)
    cid = await _new(client)
    upstream.queue_chat(
        sse(
            _reasoning_chunk("let me think"),
            _reasoning_chunk(" carefully"),
            content_chunk("the answer"),
            finish("stop"),
        )
    )
    streamed = await _consume(client, cid, "q")
    # the synthesized <think> markers + reasoning are streamed to the client as content
    assert "<think>" in streamed and "let me think" in streamed and "</think>" in streamed

    conv = (await client.get(f"/api/conversations/{cid}")).json()
    asst = [m for m in conv["messages"] if m["role"] == "assistant"][-1]
    assert asst["content"] == "<think>let me think carefully</think>\n\nthe answer"


async def test_reasoning_only_turn_closes_think(client, upstream):
    # reasoning with no answer content still produces balanced <think></think>
    await _signup(client)
    cid = await _new(client)
    upstream.queue_chat(sse(_reasoning_chunk("just pondering"), finish("stop")))
    await _consume(client, cid, "q")
    conv = (await client.get(f"/api/conversations/{cid}")).json()
    asst = [m for m in conv["messages"] if m["role"] == "assistant"][-1]
    assert asst["content"] == "<think>just pondering</think>"


async def test_reasoning_stripped_from_replay(client, upstream):
    await _signup(client)
    cid = await _new(client)
    upstream.queue_chat(sse(_reasoning_chunk("hmm"), content_chunk("A1"), finish("stop")))
    await _consume(client, cid, "q1")

    upstream.queue_chat(sse(content_chunk("A2"), finish("stop")))
    await _consume(client, cid, "q2")

    # the assistant turn replayed to the upstream on q2 must NOT carry the reasoning
    sent = upstream.chat_calls[-1]["messages"]
    asst = [m for m in sent if m["role"] == "assistant"]
    assert asst, "expected the prior assistant turn in the replay"
    assert all("<think>" not in (m["content"] or "") for m in asst)
    assert any(m["content"] == "A1" for m in asst)  # reasoning gone, answer kept


def test_strip_reasoning_unit():
    from app.conversations import _strip_reasoning

    assert _strip_reasoning("<think>r</think>answer") == "answer"
    assert _strip_reasoning("no tags here") == "no tags here"
    # unclosed trailing <think> is dropped to end (can't leak full CoT upstream)
    assert _strip_reasoning("answer <think>still thinking with <lots") == "answer"
    # depth-balanced: nested spans collapse to just the depth-0 answer, no tag leak
    assert _strip_reasoning("<think>a<think>b</think>c</think>done") == "done"
    # an orphan closer is dropped (tag removed), surrounding answer text kept
    assert _strip_reasoning("a</think>b<think>c</think>d") == "abd"
    # many unclosed opens must not hang (single linear pass); drop to end
    assert _strip_reasoning("ok " + "<think>" * 5000) == "ok"


def test_strip_reasoning_is_linear_on_closed_spans():
    # Regression for the O(n²) per-tag scan: many CLOSED spans (the absent
    # <thinking> variant used to force a rescan-to-EOF each iteration). A single
    # finditer pass strips 40k spans well under a second; cap generously at 2s.
    import time

    from app.conversations import _strip_reasoning

    payload = "<think>x</think>" * 40000
    start = time.perf_counter()
    assert _strip_reasoning(payload) == ""
    assert time.perf_counter() - start < 2.0


async def test_pure_reasoning_turn_not_replayed(client, upstream):
    await _signup(client)
    cid = await _new(client)
    upstream.queue_chat(sse(_reasoning_chunk("only pondering"), finish("stop")))
    await _consume(client, cid, "q1")
    upstream.queue_chat(sse(content_chunk("A2"), finish("stop")))
    await _consume(client, cid, "q2")
    sent = upstream.chat_calls[-1]["messages"]
    # the pure-reasoning assistant turn strips to "" and is dropped (no empty
    # assistant message that strict providers would 400 on)
    assert all(m["role"] != "assistant" or m["content"] for m in sent)


async def test_continue_rejects_pure_reasoning_turn(client, upstream):
    await _signup(client)
    cid = await _new(client)
    upstream.queue_chat(sse(_reasoning_chunk("thinking, no answer"), finish("stop")))
    await _consume(client, cid, "q1")
    conv = (await client.get(f"/api/conversations/{cid}")).json()
    asst = [m for m in conv["messages"] if m["role"] == "assistant"][-1]
    r = await client.post(f"/api/conversations/{cid}/messages/{asst['id']}/continue", json={})
    assert r.status_code == 400  # nothing to continue — it's only reasoning


async def test_inline_think_also_stripped_from_replay(client, upstream):
    # a model that emits <think> INLINE in content is handled the same way on replay
    await _signup(client)
    cid = await _new(client)
    upstream.queue_chat(sse(content_chunk("<think>inline</think>answer one"), finish("stop")))
    await _consume(client, cid, "q1")
    upstream.queue_chat(sse(content_chunk("answer two"), finish("stop")))
    await _consume(client, cid, "q2")
    sent = upstream.chat_calls[-1]["messages"]
    asst = [m for m in sent if m["role"] == "assistant"]
    assert any(m["content"] == "answer one" for m in asst)
    assert all("<think>" not in (m["content"] or "") for m in asst)
