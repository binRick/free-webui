"""Real-time channels: the broadcast hub + REST CRUD/history/post."""


class FakeWS:
    def __init__(self, fail: bool = False):
        self.sent: list = []
        self.fail = fail

    async def send_json(self, m):
        if self.fail:
            raise RuntimeError("dead socket")
        self.sent.append(m)


# ---- ChannelHub unit ----

async def test_hub_broadcast_and_disconnect():
    from app.channels import ChannelHub

    h = ChannelHub()
    a, b = FakeWS(), FakeWS()
    await h.connect("c1", a)
    await h.connect("c1", b)
    assert await h.count("c1") == 2

    await h.broadcast("c1", {"type": "message", "content": "hi"})
    assert a.sent == [{"type": "message", "content": "hi"}]
    assert b.sent == [{"type": "message", "content": "hi"}]

    await h.disconnect("c1", a)
    assert await h.count("c1") == 1
    await h.broadcast("c1", {"x": 1})
    assert len(a.sent) == 1  # a no longer receives
    assert b.sent[-1] == {"x": 1}


async def test_hub_per_user_connection_cap():
    from app.channels import ChannelHub

    h = ChannelHub()
    assert await h.try_reserve(7, cap=2) is True
    assert await h.try_reserve(7, cap=2) is True
    assert await h.try_reserve(7, cap=2) is False  # at cap
    assert await h.try_reserve(8, cap=2) is True  # a different user is independent
    await h.release(7)
    assert await h.try_reserve(7, cap=2) is True  # a slot freed up


def test_rate_allow_sliding_window():
    from collections import deque

    from app.channels import _rate_allow

    frames: deque = deque()
    # 3 allowed in the window, the 4th blocked
    assert _rate_allow(frames, 100.0, 3, 10.0) is True
    assert _rate_allow(frames, 100.5, 3, 10.0) is True
    assert _rate_allow(frames, 101.0, 3, 10.0) is True
    assert _rate_allow(frames, 101.5, 3, 10.0) is False
    # once the window slides past the earliest entries, room opens again
    assert _rate_allow(frames, 112.0, 3, 10.0) is True


async def test_hub_isolates_channels_and_drops_dead():
    from app.channels import ChannelHub

    h = ChannelHub()
    a, dead, other = FakeWS(), FakeWS(fail=True), FakeWS()
    await h.connect("c1", a)
    await h.connect("c1", dead)
    await h.connect("c2", other)

    await h.broadcast("c1", {"m": 1})
    assert a.sent == [{"m": 1}]
    assert other.sent == []  # different channel
    # the dead socket raised and was pruned
    assert await h.count("c1") == 1


async def test_slow_socket_does_not_block_others(monkeypatch):
    """A backpressured (slow-but-open) socket must time out and be dropped
    without stalling the fan-out for everyone else — critical under Redis, where
    one shared subscriber task drains every channel."""
    import asyncio
    import time

    import app.channels as channels_mod
    from app.channels import ChannelHub

    monkeypatch.setattr(channels_mod, "_SEND_TIMEOUT", 0.1)
    h = ChannelHub()

    class SlowWS:
        async def send_json(self, m):
            await asyncio.sleep(5)  # never completes within the timeout

    fast = FakeWS()
    await h.connect("c1", SlowWS())
    await h.connect("c1", fast)

    start = time.monotonic()
    await h.broadcast("c1", {"type": "message", "content": "hi"})
    elapsed = time.monotonic() - start

    assert fast.sent == [{"type": "message", "content": "hi"}]  # fast client served
    assert elapsed < 1.0  # did NOT wait 5s for the slow socket (timed out at 0.1s)
    assert await h.count("c1") == 1  # slow socket dropped; fast remains


# ---- REST ----

async def _signup(client, username="alice", password="hunter22hunter"):
    await client.post("/api/auth/setup", json={"username": username, "password": password})


async def _make_user(client, username):
    return (
        await client.post(
            "/api/admin/users", json={"username": username, "password": "passpass", "role": "user"}
        )
    ).json()["id"]


async def _login(client, username, password="passpass"):
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": username, "password": password})


async def test_channel_crud_and_messages(client):
    await _signup(client)
    assert (await client.get("/api/channels")).json() == []

    ch = (await client.post("/api/channels", json={"name": "general", "description": "all"})).json()
    cid = ch["id"]
    assert ch["name"] == "general"
    assert [c["id"] for c in (await client.get("/api/channels")).json()] == [cid]
    assert (await client.get(f"/api/channels/{cid}")).json()["name"] == "general"

    for t in ["first", "second", "third"]:
        r = await client.post(f"/api/channels/{cid}/messages", json={"content": t})
        assert r.status_code == 200
    msgs = (await client.get(f"/api/channels/{cid}/messages")).json()
    assert [m["content"] for m in msgs] == ["first", "second", "third"]  # oldest-first
    assert all(m["username"] == "alice" for m in msgs)


async def test_message_history_pagination(client):
    await _signup(client)
    cid = (await client.post("/api/channels", json={"name": "c"})).json()["id"]
    for i in range(5):
        await client.post(f"/api/channels/{cid}/messages", json={"content": f"m{i}"})
    page = (await client.get(f"/api/channels/{cid}/messages?limit=2")).json()
    assert [m["content"] for m in page] == ["m3", "m4"]  # newest 2, oldest-first
    before = page[0]["id"]
    older = (await client.get(f"/api/channels/{cid}/messages?before={before}&limit=2")).json()
    assert [m["content"] for m in older] == ["m1", "m2"]


async def test_post_message_broadcasts_to_hub(client):
    await _signup(client)
    from app.channels import hub

    cid = (await client.post("/api/channels", json={"name": "c"})).json()["id"]
    sub = FakeWS()
    await hub.connect(cid, sub)
    await client.post(f"/api/channels/{cid}/messages", json={"content": "live!"})
    assert len(sub.sent) == 1
    assert sub.sent[0]["type"] == "message"
    assert sub.sent[0]["content"] == "live!"
    assert sub.sent[0]["username"] == "alice"


async def test_channel_delete_permissions(client):
    await _signup(client)  # alice is admin
    await _make_user(client, "bob")
    # bob creates a channel
    await _login(client, "bob")
    cid = (await client.post("/api/channels", json={"name": "bobs"})).json()["id"]
    # a third user can't delete it
    await client.post("/api/auth/login", json={"username": "alice", "password": "hunter22hunter"})
    await _make_user(client, "carol")
    await _login(client, "carol")
    assert (await client.delete(f"/api/channels/{cid}")).status_code == 403
    # bob (creator) can
    await _login(client, "bob")
    assert (await client.delete(f"/api/channels/{cid}")).status_code == 204


async def test_admin_can_delete_any_channel(client):
    await _signup(client)  # admin
    await _make_user(client, "bob")
    await _login(client, "bob")
    cid = (await client.post("/api/channels", json={"name": "bobs"})).json()["id"]
    await client.post("/api/auth/login", json={"username": "alice", "password": "hunter22hunter"})
    assert (await client.delete(f"/api/channels/{cid}")).status_code == 204


async def test_channels_require_auth_and_validate(client):
    # unauthenticated
    assert (await client.get("/api/channels")).status_code == 401
    await _signup(client)
    # validation
    assert (await client.post("/api/channels", json={"name": ""})).status_code == 422
    cid = (await client.post("/api/channels", json={"name": "c"})).json()["id"]
    assert (await client.post(f"/api/channels/{cid}/messages", json={"content": ""})).status_code == 422
    # unknown channel
    assert (await client.get("/api/channels/nope/messages")).status_code == 404
    assert (await client.post("/api/channels/nope/messages", json={"content": "x"})).status_code == 404
