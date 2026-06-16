"""Pluggable fan-out transport for the real-time channel hub.

Two implementations behind one tiny interface:

* :class:`LocalBroadcaster` — in-process (the zero-config default). ``publish``
  delivers a frame straight to this process's local WebSockets. Single-process
  only, but needs no external service and no dependency.
* :class:`RedisBroadcaster` — cross-replica. ``publish`` PUBLISHes the frame to a
  Redis channel; a background subscriber on EVERY replica (including the
  publisher) receives it and delivers to that replica's local WebSockets. This
  is what lets N stateless app replicas share channel traffic.

Both call the same ``deliver_local(channel_id, frame)`` coroutine the hub owns,
so the hub's socket bookkeeping is identical regardless of transport. ``redis``
is an optional dependency, imported lazily only when a Redis URL is configured
(mirroring the optional asyncpg Postgres backend).

Scope note: this fans out message/typing/presence FRAMES across replicas. The
numeric presence ``online`` count and the per-user connection cap remain
per-replica (each replica counts only its own sockets); a globally-accurate
count would need shared Redis counters with liveness TTLs — see docs/SCALING.md.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable

log = logging.getLogger("free_webui.broadcaster")

Deliver = Callable[[str, dict], Awaitable[None]]

_CHANNEL_PREFIX = "freewebui:channel:"
_INITIAL_BACKOFF = 0.5
_MAX_BACKOFF = 15.0


class LocalBroadcaster:
    """In-process fan-out: publish == deliver locally."""

    def __init__(self, deliver: Deliver) -> None:
        self._deliver = deliver

    async def publish(self, channel_id: str, frame: dict) -> None:
        await self._deliver(channel_id, frame)

    async def aclose(self) -> None:  # nothing to tear down
        return None


class RedisBroadcaster:
    """Cross-replica fan-out over Redis pub/sub.

    The publisher does NOT deliver locally itself — it relies on its own
    subscription receiving the published frame, so every replica (publisher
    included) delivers through exactly one path. That keeps delivery single and
    ordering consistent.
    """

    def __init__(self, url: str, deliver: Deliver) -> None:
        self._url = url
        self._deliver = deliver
        self._redis = None
        self._pubsub = None
        self._task: asyncio.Task | None = None
        self._closing = False

    async def start(self) -> None:
        import redis.asyncio as aioredis  # lazy: only needed when Redis is configured

        self._redis = aioredis.from_url(self._url)
        await self._subscribe()  # initial subscription is live before we return
        self._task = asyncio.create_task(self._reader())

    async def _subscribe(self) -> None:
        assert self._redis is not None
        self._pubsub = self._redis.pubsub()
        await self._pubsub.psubscribe(_CHANNEL_PREFIX + "*")

    def _handle(self, msg: dict) -> tuple[str, dict] | None:
        if msg.get("type") != "pmessage":
            return None
        ch = msg["channel"]
        if isinstance(ch, bytes):
            ch = ch.decode("utf-8", "replace")
        raw = msg["data"]
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        try:
            frame = json.loads(raw)
        except (ValueError, TypeError):
            return None  # ignore a malformed payload rather than killing the loop
        return ch[len(_CHANNEL_PREFIX):], frame

    async def _reader(self) -> None:
        """Supervised subscribe→listen loop. A dropped Redis connection is the
        SOLE delivery path on a replica, so it must self-heal: on error we log,
        rebuild the subscription, and retry with bounded backoff (rather than
        dying silently and leaving every channel dark until a restart)."""
        backoff = _INITIAL_BACKOFF
        while not self._closing:
            try:
                if self._pubsub is None:
                    await self._subscribe()
                async for msg in self._pubsub.listen():
                    backoff = _INITIAL_BACKOFF  # healthy traffic → reset backoff
                    parsed = self._handle(msg)
                    if parsed is None:
                        continue
                    try:
                        await self._deliver(*parsed)
                    except Exception:
                        continue  # one bad delivery must not stop the subscriber
                if self._closing:
                    break
            except asyncio.CancelledError:
                raise
            except Exception:
                if self._closing:
                    break
                log.warning("redis channel subscriber dropped; reconnecting", exc_info=True)
                await self._drop_pubsub()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF)

    async def publish(self, channel_id: str, frame: dict) -> None:
        if self._redis is None:
            return
        await self._redis.publish(_CHANNEL_PREFIX + channel_id, json.dumps(frame))

    async def _drop_pubsub(self) -> None:
        if self._pubsub is not None:
            try:
                await self._pubsub.aclose()
            except Exception:
                pass
            self._pubsub = None

    async def aclose(self) -> None:
        self._closing = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass  # expected: we cancelled the child
            except Exception:
                log.warning("redis subscriber teardown error", exc_info=True)
        await self._drop_pubsub()
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
