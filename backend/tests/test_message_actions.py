"""Per-message delete (truncate-from-here) and regenerate of any (not just the
trailing) assistant turn."""
from tests.conftest import content_chunk, finish, sse


async def _signup(client):
    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})


async def _consume(client, method, path, body=None):
    async with client.stream(method, path, json=body or {}) as r:
        assert r.status_code == 200, await r.aread()
        async for _ in r.aiter_bytes():
            pass


async def _new(client):
    return (await client.post("/api/conversations", json={})).json()["id"]


async def _msgs(client, cid):
    return (await client.get(f"/api/conversations/{cid}")).json()["messages"]


async def _two_turns(client, cid):
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", {"content": "one"})
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", {"content": "two"})


# ---- delete (truncate) ----

async def test_delete_truncates_from_here(client):
    await _signup(client)
    cid = await _new(client)
    await _two_turns(client, cid)
    msgs = await _msgs(client, cid)
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    second_user = msgs[2]["id"]

    r = await client.delete(f"/api/conversations/{cid}/messages/{second_user}")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert [m["content"].strip() for m in await _msgs(client, cid)] == ["one", "echo: one"]


async def test_delete_single_trailing_message(client):
    await _signup(client)
    cid = await _new(client)
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", {"content": "hi"})
    msgs = await _msgs(client, cid)
    assistant_id = msgs[1]["id"]
    await client.delete(f"/api/conversations/{cid}/messages/{assistant_id}")
    assert [m["role"] for m in await _msgs(client, cid)] == ["user"]


async def test_delete_unknown_message_404(client):
    await _signup(client)
    cid = await _new(client)
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", {"content": "hi"})
    assert (await client.delete(f"/api/conversations/{cid}/messages/99999")).status_code == 404


async def test_delete_requires_ownership(client):
    await _signup(client)
    cid = await _new(client)
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", {"content": "hi"})
    mid = (await _msgs(client, cid))[0]["id"]
    await client.post(
        "/api/admin/users", json={"username": "bob", "password": "passpass", "role": "user"}
    )
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": "bob", "password": "passpass"})
    # bob can't even see the conversation -> 404 (not 403, no existence leak)
    assert (await client.delete(f"/api/conversations/{cid}/messages/{mid}")).status_code == 404


# ---- regenerate any assistant turn ----

async def test_regenerate_mid_thread_branches(client, upstream):
    await _signup(client)
    cid = await _new(client)
    await _two_turns(client, cid)
    first_assistant = (await _msgs(client, cid))[1]["id"]

    upstream.queue_chat(sse(content_chunk("regenerated one"), finish("stop")))
    await _consume(
        client, "POST", f"/api/conversations/{cid}/messages/{first_assistant}/regenerate"
    )

    msgs = await _msgs(client, cid)
    # the later turn (u2/a2) is discarded; turn 1's reply is replaced
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert [m["content"].strip() for m in msgs] == ["one", "regenerated one"]

    # the prior reply is preserved as an archived variant of the new trailing one
    active_id = msgs[1]["id"]
    variants = (
        await client.get(f"/api/conversations/{cid}/messages/{active_id}/variants")
    ).json()["variants"]
    assert [v["active"] for v in variants] == [False, True]


async def test_regenerate_user_message_400(client):
    await _signup(client)
    cid = await _new(client)
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", {"content": "hi"})
    user_id = (await _msgs(client, cid))[0]["id"]
    r = await client.post(f"/api/conversations/{cid}/messages/{user_id}/regenerate", json={})
    assert r.status_code == 400


async def test_regenerate_unknown_message_404(client):
    await _signup(client)
    cid = await _new(client)
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", {"content": "hi"})
    r = await client.post(f"/api/conversations/{cid}/messages/55555/regenerate", json={})
    assert r.status_code == 404


# ---- continue generation ----

async def test_continue_appends_to_trailing_assistant(client, upstream):
    from tests.conftest import content_chunk, finish, sse

    await _signup(client)
    cid = await _new(client)
    # first reply ends mid-thought
    upstream.queue_chat(sse(content_chunk("The capital of France"), finish("length")))
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", {"content": "capital?"})
    msgs = await _msgs(client, cid)
    assistant_id = msgs[1]["id"]
    assert msgs[1]["content"] == "The capital of France"

    # continue → the streamed text is appended onto the SAME message
    upstream.queue_chat(sse(content_chunk(" is Paris."), finish("stop")))
    await _consume(client, "POST", f"/api/conversations/{cid}/messages/{assistant_id}/continue")

    msgs = await _msgs(client, cid)
    # still exactly one assistant message, now extended
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["id"] == assistant_id
    assert msgs[1]["content"] == "The capital of France is Paris."


async def test_continue_replays_partial_and_instruction_upstream(client, upstream):
    from tests.conftest import content_chunk, finish, sse

    await _signup(client)
    cid = await _new(client)
    upstream.queue_chat(sse(content_chunk("partial answer"), finish("length")))
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", {"content": "q"})
    aid = (await _msgs(client, cid))[1]["id"]

    upstream.queue_chat(sse(content_chunk(" continued"), finish("stop")))
    await _consume(client, "POST", f"/api/conversations/{cid}/messages/{aid}/continue")

    # the continue request replayed the partial assistant reply + a continue nudge
    last = upstream.chat_calls[-1]["messages"]
    assert last[-2]["role"] == "assistant" and last[-2]["content"] == "partial answer"
    assert last[-1]["role"] == "user" and "Continue" in last[-1]["content"]


async def test_continue_only_trailing_assistant(client):
    await _signup(client)
    cid = await _new(client)
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", {"content": "one"})
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", {"content": "two"})
    msgs = await _msgs(client, cid)
    first_assistant = msgs[1]["id"]
    user_msg = msgs[0]["id"]
    # not the trailing turn
    assert (
        await client.post(f"/api/conversations/{cid}/messages/{first_assistant}/continue", json={})
    ).status_code == 400
    # not an assistant message
    assert (
        await client.post(f"/api/conversations/{cid}/messages/{user_msg}/continue", json={})
    ).status_code == 400
