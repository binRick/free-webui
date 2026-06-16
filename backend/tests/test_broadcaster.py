"""Channel fan-out transport: in-process default + cross-replica Redis pub/sub."""
import asyncio
import os

import pytest

from app import broadcaster
from app.broadcaster import _CHANNEL_PREFIX, LocalBroadcaster, RedisBroadcaster


def _recorder(sink: list):
    async def deliver(channel_id: str, frame: dict) -> None:
        sink.append((channel_id, frame))
    return deliver


class _ScriptedPubSub:
    """A pubsub whose listen() either drops (raises) or delivers one frame then
    parks — to drive the reconnect supervisor deterministically without Redis."""

    def __init__(self, action: str):
        self.action = action

    async def psubscribe(self, *a, **k):
        return None

    async def aclose(self):
        return None

    async def listen(self):
        if self.action == "raise":
            raise ConnectionError("simulated redis drop")
        yield {
            "type": "pmessage",
            "channel": (_CHANNEL_PREFIX + "room").encode(),
            "data": b'{"type":"message","content":"after-reconnect"}',
        }
        await asyncio.Event().wait()  # park until cancelled


class _ScriptedRedis:
    def __init__(self, actions):
        self._actions = list(actions)

    def pubsub(self):
        return _ScriptedPubSub(self._actions.pop(0))

    async def aclose(self):
        return None


async def test_redis_reader_reconnects_after_drop(monkeypatch):
    """The subscriber is a replica's SOLE delivery path, so a dropped connection
    must self-heal: after a ConnectionError it re-subscribes and resumes
    delivering, instead of dying silently."""
    monkeypatch.setattr(broadcaster, "_INITIAL_BACKOFF", 0.01)
    delivered: list = []
    b = RedisBroadcaster("redis://unused", _recorder(delivered))
    b._redis = _ScriptedRedis(["raise", "deliver"])  # first listen drops, second delivers
    b._task = asyncio.create_task(b._reader())
    try:
        for _ in range(100):
            if delivered:
                break
            await asyncio.sleep(0.02)
    finally:
        b._closing = True
        b._task.cancel()
        try:
            await b._task
        except asyncio.CancelledError:
            pass
    assert delivered and delivered[0][0] == "room"
    assert delivered[0][1]["content"] == "after-reconnect"  # delivery resumed post-drop


async def test_local_broadcaster_delivers_in_process():
    sink: list = []
    b = LocalBroadcaster(_recorder(sink))
    await b.publish("room1", {"type": "message", "content": "hi"})
    await b.aclose()
    assert sink == [("room1", {"type": "message", "content": "hi"})]


async def test_hub_broadcast_reaches_local_delivery():
    """hub.broadcast() must route through the (default Local) transport back to
    _deliver_local — i.e. the refactor preserved in-process fan-out."""
    from app.channels import ChannelHub

    h = ChannelHub()
    seen: list = []

    async def fake_deliver(channel_id: str, frame: dict) -> None:
        seen.append((channel_id, frame))

    h._deliver_local = fake_deliver  # type: ignore[assignment]
    h._broadcaster = LocalBroadcaster(fake_deliver)
    await h.broadcast("c", {"type": "typing", "username": "alice"})
    assert seen == [("c", {"type": "typing", "username": "alice"})]


# ---- optional: real Redis cross-replica fan-out (gated; set FREE_WEBUI_TEST_REDIS=1) ----

@pytest.mark.skipif(not os.getenv("FREE_WEBUI_TEST_REDIS"), reason="set FREE_WEBUI_TEST_REDIS + a Redis")
async def test_redis_broadcaster_fans_out_across_instances():
    """Two RedisBroadcaster instances (two simulated replicas) sharing one Redis:
    a frame published on one is delivered to BOTH (the publisher gets it back via
    its own subscription, the peer via pub/sub) — proving cross-replica delivery."""
    url = os.getenv("FREE_WEBUI_TEST_REDIS_URL", "redis://localhost:56379/0")
    seen_a: list = []
    seen_b: list = []
    a = RedisBroadcaster(url, _recorder(seen_a))
    b = RedisBroadcaster(url, _recorder(seen_b))
    await a.start()
    await b.start()
    try:
        await asyncio.sleep(0.3)  # let both psubscribe settle
        await a.publish("room42", {"type": "message", "content": "cross-replica"})
        for _ in range(60):
            if seen_a and seen_b:
                break
            await asyncio.sleep(0.05)
    finally:
        await a.aclose()
        await b.aclose()

    expected = ("room42", {"type": "message", "content": "cross-replica"})
    assert expected in seen_a  # publisher delivers via its own subscription
    assert expected in seen_b  # the peer replica receives it too


class _FakeWS:
    def __init__(self):
        self.sent: list = []

    async def send_json(self, m):
        self.sent.append(m)


@pytest.mark.skipif(not os.getenv("FREE_WEBUI_TEST_REDIS"), reason="set FREE_WEBUI_TEST_REDIS + a Redis")
async def test_posted_message_round_trips_through_redis(client):
    """End-to-end: with the hub upgraded to Redis, a REST-posted message is
    published to Redis and delivered back to a local subscriber via the
    subscriber task (no direct local send) — the full replica round-trip."""
    from app.channels import hub

    url = os.getenv("FREE_WEBUI_TEST_REDIS_URL", "redis://localhost:56379/0")
    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})
    cid = (await client.post("/api/channels", json={"name": "c"})).json()["id"]
    await hub.use_redis(url)
    try:
        await asyncio.sleep(0.3)  # subscriber settles
        sub = _FakeWS()
        await hub.connect(cid, sub)
        await client.post(f"/api/channels/{cid}/messages", json={"content": "via-redis"})
        for _ in range(60):
            if sub.sent:
                break
            await asyncio.sleep(0.05)
    finally:
        await hub.aclose()
    assert sub.sent and sub.sent[0]["type"] == "message"
    assert sub.sent[0]["content"] == "via-redis"
