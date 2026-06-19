"""Multi-statement mutations (edit/regenerate/delete) are wrapped in
db.transaction(), so a failure midway rolls the whole unit back instead of
leaving the shared aiosqlite connection holding pending writes that the next
request's commit would silently flush."""
import pytest


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


async def test_edit_failure_rolls_back_and_does_not_leak(client, monkeypatch):
    await _signup(client)
    cid = await _new(client)
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", {"content": "one"})
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", {"content": "two"})
    msgs = await _msgs(client, cid)
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    first_user_id = msgs[0]["id"]

    # Make the GC step INSIDE edit's transaction blow up, after the UPDATE+DELETE.
    # (Import here, not at module top: the client fixture re-imports app.* per
    # test, so the live module object only exists after the fixture has run.)
    import app.conversations as C

    async def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(C, "gc_orphan_files", boom)
    # The test ASGI transport re-raises unhandled app exceptions (in production
    # the global handler turns this into a 500). Either way the transaction's
    # __aexit__ has already rolled the pending UPDATE+DELETE back.
    with pytest.raises(RuntimeError):
        await client.patch(
            f"/api/conversations/{cid}/messages/{first_user_id}", json={"content": "HACKED"}
        )

    # The edit (content rewrite + tail truncation) was rolled back wholesale.
    after = await _msgs(client, cid)
    assert [m["role"] for m in after] == ["user", "assistant", "user", "assistant"]
    assert after[0]["content"] == "one"

    # And — the key guarantee — the rolled-back writes don't leak into the NEXT
    # request's commit: a subsequent normal send must not resurrect "HACKED" or
    # the truncation.
    monkeypatch.undo()
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", {"content": "three"})
    final = await _msgs(client, cid)
    assert final[0]["content"] == "one"
    assert not any("HACKED" in (m["content"] or "") for m in final)
    assert [m["role"] for m in final] == [
        "user", "assistant", "user", "assistant", "user", "assistant",
    ]
