"""Cross-replica per-user WS connection cap + presence count (Redis-backed).

Two ChannelHub instances sharing one Redis client model two app replicas; the
cap and the presence count must be GLOBAL across them, not per-replica. Falls
back to the in-process counters on any Redis hiccup.
"""
import pytest

from app.channels import ChannelHub


class FakeRedis:
    """Minimal in-memory async Redis: int counters with INCR/DECR/GET/SET.
    Shared by multiple hubs to simulate replicas hitting one Redis."""

    def __init__(self) -> None:
        self.store: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def decr(self, key: str) -> int:
        self.store[key] = self.store.get(key, 0) - 1
        return self.store[key]

    async def expire(self, key: str, ttl: int) -> bool:
        return True

    async def get(self, key: str):
        v = self.store.get(key)
        return None if v is None else str(v).encode()  # real redis-py returns bytes

    async def set(self, key: str, value) -> bool:
        self.store[key] = int(value)
        return True


class BrokenRedis(FakeRedis):
    async def incr(self, key: str) -> int:
        raise RuntimeError("redis down")


pytestmark = pytest.mark.asyncio


async def test_cap_is_global_across_replicas():
    redis = FakeRedis()
    a, b = ChannelHub(), ChannelHub()
    a.configure_redis(redis)
    b.configure_redis(redis)

    cap = 3
    # 3 reservations spread across the two "replicas" all succeed...
    assert await a.try_reserve(7, cap) is True
    assert await b.try_reserve(7, cap) is True
    assert await a.try_reserve(7, cap) is True
    # ...the 4th is refused regardless of which replica it hits.
    assert await b.try_reserve(7, cap) is False
    assert await a.try_reserve(7, cap) is False

    # releasing on either replica frees a global slot.
    await b.release(7)
    assert await a.try_reserve(7, cap) is True


async def test_release_floors_at_zero():
    redis = FakeRedis()
    hub = ChannelHub()
    hub.configure_redis(redis)
    # more releases than reserves must not drive the counter negative.
    await hub.release(9)
    await hub.release(9)
    assert redis.store.get("fw:wsconn:9", 0) == 0
    assert await hub.try_reserve(9, 1) is True


async def test_presence_count_is_global():
    redis = FakeRedis()
    a, b = ChannelHub(), ChannelHub()
    a.configure_redis(redis)
    b.configure_redis(redis)

    s1, s2, s3 = object(), object(), object()  # stand-in sockets
    await a.connect("room", s1)
    await a.connect("room", s2)
    await b.connect("room", s3)
    # both replicas report the GLOBAL count (3), not just their local sockets.
    assert await a.count("room") == 3
    assert await b.count("room") == 3

    await a.disconnect("room", s1)
    assert await b.count("room") == 2


async def test_double_disconnect_does_not_overcount():
    redis = FakeRedis()
    hub = ChannelHub()
    hub.configure_redis(redis)
    s1 = object()
    await hub.connect("room", s1)
    await hub.disconnect("room", s1)
    await hub.disconnect("room", s1)  # second disconnect (already gone) is a no-op
    assert await hub.count("room") == 0


async def test_redis_error_fails_open():
    hub = ChannelHub()
    hub.configure_redis(BrokenRedis())
    # incr raises → fail OPEN (allow the connection): the posture is "a Redis
    # hiccup never locks a user out / errors a connection". The cap simply isn't
    # enforced during the outage (it self-heals when Redis recovers). Crucially,
    # the in-process counter is NOT touched, so there's no cross-store drift.
    assert await hub.try_reserve(1, 1) is True
    assert await hub.try_reserve(1, 1) is True
    assert hub._user_counts == {}  # in-process store untouched under Redis


async def test_no_redis_is_per_process():
    hub = ChannelHub()  # unconfigured → in-process
    assert await hub.try_reserve(1, 2) is True
    assert await hub.try_reserve(1, 2) is True
    assert await hub.try_reserve(1, 2) is False
    s = object()
    await hub.connect("room", s)
    assert await hub.count("room") == 1
