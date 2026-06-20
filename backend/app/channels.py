"""Real-time chat channels: shared multi-user rooms with live WebSocket delivery.

Workspace-wide — every authenticated user can list, read, and post. REST handles
CRUD + history + posting (persist then broadcast); a per-channel WebSocket pushes
live ``message``/``typing``/``presence`` frames and also accepts inbound
``message``/``typing`` for true real-time posting.

The broadcast hub fans frames out through a pluggable transport: in-process by
default (a module-level singleton, zero-config), or Redis pub/sub across replicas
when ``FREE_WEBUI_REDIS_URL`` is set (see :mod:`app.broadcaster`).
"""

import asyncio
import time
import uuid
from collections import deque
from typing import Any

import aiosqlite
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel, Field

from .auth import SESSION_COOKIE, current_user, read_session
from .broadcaster import LocalBroadcaster, RedisBroadcaster
from .config import settings

router = APIRouter(prefix="/api/channels", tags=["channels"])

_MAX_CONTENT = 4000
# Per-connection inbound frame budget (covers message + typing) — bounds DB
# writes / broadcasts a single socket can drive.
_FRAME_RATE_MAX = 20
_FRAME_RATE_WINDOW = 10.0
# Per-socket send deadline. A backpressured (slow-but-open) client must not stall
# the fan-out — under Redis there is ONE subscriber task draining all channels,
# so a single hung send would freeze delivery everywhere. Time out → treat dead.
_SEND_TIMEOUT = 10.0

# Redis keys for the cross-replica per-user connection counter and per-channel
# presence counter. Each carries a generous TTL refreshed on every change, so a
# crashed replica that never decrements self-heals after a quiet window rather
# than leaking a count forever (the cap is a coarse backstop, not exact).
_WSCONN_PREFIX = "fw:wsconn:"
_PRESENCE_PREFIX = "fw:presence:"
_COUNTER_TTL = 3600


class ChannelHub:
    """Tracks live WebSocket subscribers per channel and fans messages out."""

    def __init__(self) -> None:
        self._channels: dict[str, set[WebSocket]] = {}
        self._user_counts: dict[int, int] = {}
        self._lock = asyncio.Lock()
        # Fan-out transport. Defaults to in-process; swapped for Redis at startup
        # when configured. deliver_local is what actually writes to local sockets.
        self._broadcaster = LocalBroadcaster(self._deliver_local)
        # Optional shared Redis client (set at startup): makes the per-user
        # connection cap and the presence count GLOBAL across replicas instead of
        # per-replica. None → the in-process counters below (single-replica).
        self._redis = None

    def configure_redis(self, client) -> None:
        """Use ``client`` for the global cap + presence counters (or None to
        revert to in-process). Shares the app's Redis client, like the limiter."""
        self._redis = client

    # Invariant: when ``self._redis`` is set, the in-process counters below are
    # NEVER touched — Redis is the single source of truth and a transient Redis
    # error fails toward AVAILABILITY (never locks a user out). This avoids any
    # cross-store drift (a slot reserved in one store, released in the other).

    async def try_reserve(self, user_id: int, cap: int) -> bool:
        """Reserve a connection slot for a user, capping concurrent sockets
        (globally when Redis is configured, else per-replica)."""
        if self._redis is not None:
            return await self._redis_reserve(f"{_WSCONN_PREFIX}{user_id}", cap)
        async with self._lock:
            if self._user_counts.get(user_id, 0) >= cap:
                return False
            self._user_counts[user_id] = self._user_counts.get(user_id, 0) + 1
            return True

    async def _redis_reserve(self, key: str, cap: int) -> bool:
        try:
            n = await self._redis.incr(key)
        except Exception:
            return True  # Redis down → fail OPEN (availability); never lock out
        if n == 1:
            # Set the TTL only at creation (not on every change) so a leaked
            # counter ages out within one window instead of being refreshed alive
            # forever — the self-heal the design depends on.
            await self._safe(self._redis.expire(key, _COUNTER_TTL))
        if n > cap:
            await self._safe(self._redis.decr(key))  # leak (if this fails) ages out via TTL
            return False
        return True

    async def release(self, user_id: int) -> None:
        if self._redis is not None:
            await self._redis_decr(f"{_WSCONN_PREFIX}{user_id}")
            return
        async with self._lock:
            n = self._user_counts.get(user_id, 0) - 1
            if n <= 0:
                self._user_counts.pop(user_id, None)
            else:
                self._user_counts[user_id] = n

    async def _redis_decr(self, key: str) -> None:
        """DECR a Redis counter, flooring at 0 with a COMPENSATING incr (not a
        blind SET, which would clobber a concurrent incr on another replica). A
        Redis error is swallowed — the create-time TTL reclaims any leaked slot."""
        try:
            n = await self._redis.decr(key)
            if n < 0:
                await self._redis.incr(key)  # undo only this over-decrement
        except Exception:
            pass

    @staticmethod
    async def _safe(coro) -> None:
        try:
            await coro
        except Exception:
            pass

    async def connect(self, channel_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._channels.setdefault(channel_id, set()).add(ws)
        if self._redis is not None:
            key = f"{_PRESENCE_PREFIX}{channel_id}"
            try:
                n = await self._redis.incr(key)
            except Exception:
                return
            if n == 1:
                await self._safe(self._redis.expire(key, _COUNTER_TTL))

    async def disconnect(self, channel_id: str, ws: WebSocket) -> None:
        # Decrement presence only when this socket was actually present, so a
        # double disconnect (dead-socket reap in _deliver_local + the handler's
        # finally) can't over-decrement the shared counter.
        removed = False
        async with self._lock:
            conns = self._channels.get(channel_id)
            if conns and ws in conns:
                conns.discard(ws)
                removed = True
                if not conns:
                    self._channels.pop(channel_id, None)
        if removed and self._redis is not None:
            await self._redis_decr(f"{_PRESENCE_PREFIX}{channel_id}")

    async def count(self, channel_id: str) -> int:
        if self._redis is not None:
            try:
                v = await self._redis.get(f"{_PRESENCE_PREFIX}{channel_id}")
                if v is not None:
                    # redis-py GET returns bytes by default; decode before int().
                    if isinstance(v, (bytes, bytearray)):
                        v = v.decode()
                    return max(0, int(v))
            except Exception:
                pass
        async with self._lock:
            return len(self._channels.get(channel_id, ()))

    async def broadcast(self, channel_id: str, message: dict) -> None:
        """Publish a frame to all subscribers of a channel, across replicas when
        Redis is configured. Delegates to the transport, which calls back into
        :meth:`_deliver_local` on each replica."""
        await self._broadcaster.publish(channel_id, message)

    async def _deliver_local(self, channel_id: str, message: dict) -> None:
        # Snapshot under the lock, send outside it so a slow/dead socket can't
        # block the whole fan-out or deadlock against (dis)connect. Sends run
        # CONCURRENTLY with a per-socket deadline so one backpressured client
        # can't serialize-stall the others (or, under Redis, the shared
        # subscriber that drains every channel).
        async with self._lock:
            conns = list(self._channels.get(channel_id, ()))
        if not conns:
            return

        async def _send(ws: WebSocket) -> WebSocket | None:
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=_SEND_TIMEOUT)
                return None
            except Exception:
                return ws  # errored or timed out → treat as dead

        results = await asyncio.gather(*(_send(ws) for ws in conns))
        for ws in results:
            if ws is not None:
                await self.disconnect(channel_id, ws)

    async def use_redis(self, url: str) -> None:
        """Upgrade the transport to Redis pub/sub (called at startup when
        ``FREE_WEBUI_REDIS_URL`` is set). Idempotent: tears down any prior
        transport before swapping, so a repeat call can't orphan a subscriber
        task or leak Redis connections."""
        old = self._broadcaster
        broadcaster = RedisBroadcaster(url, self._deliver_local)
        await broadcaster.start()
        self._broadcaster = broadcaster
        await old.aclose()

    async def aclose(self) -> None:
        await self._broadcaster.aclose()


hub = ChannelHub()


# ---- models ----

class ChannelIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=300)


class ChannelOut(BaseModel):
    id: str
    name: str
    description: str | None
    created_by: int | None
    created_at: int


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=_MAX_CONTENT)


class MessageOut(BaseModel):
    id: int
    channel_id: str
    user_id: int | None
    username: str
    content: str
    created_at: int


def _rate_allow(frames: deque, now: float, max_n: int, window: float) -> bool:
    """Sliding-window limiter: True (and records `now`) if under the budget."""
    while frames and now - frames[0] > window:
        frames.popleft()
    if len(frames) >= max_n:
        return False
    frames.append(now)
    return True


def _db(request: Request) -> aiosqlite.Connection:
    return request.app.state.db


async def _require_channel(db: aiosqlite.Connection, channel_id: str) -> None:
    cur = await db.execute("SELECT 1 FROM channels WHERE id = ?", (channel_id,))
    if not await cur.fetchone():
        raise HTTPException(status_code=404, detail="channel not found")


async def _persist_message(
    db: aiosqlite.Connection, channel_id: str, user: dict, content: str
) -> dict:
    ts = int(time.time())
    msg_id = await db.insert(
        "INSERT INTO channel_messages (channel_id, user_id, username, content, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (channel_id, user["id"], user["username"], content, ts),
    )
    await db.commit()
    return {
        "id": msg_id,
        "channel_id": channel_id,
        "user_id": user["id"],
        "username": user["username"],
        "content": content,
        "created_at": ts,
    }


# ---- REST ----

@router.get("", response_model=list[ChannelOut], dependencies=[Depends(current_user)])
async def list_channels(request: Request) -> list[ChannelOut]:
    cur = await _db(request).execute(
        "SELECT id, name, description, created_by, created_at FROM channels "
        "ORDER BY created_at DESC"
    )
    return [
        ChannelOut(id=r[0], name=r[1], description=r[2], created_by=r[3], created_at=r[4])
        for r in await cur.fetchall()
    ]


@router.post("", response_model=ChannelOut)
async def create_channel(
    body: ChannelIn, request: Request, user: dict = Depends(current_user)
) -> ChannelOut:
    db = _db(request)
    cid = uuid.uuid4().hex
    now = int(time.time())
    name = body.name.strip()
    await db.execute(
        "INSERT INTO channels (id, name, description, created_by, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (cid, name, body.description, user["id"], now),
    )
    await db.commit()
    return ChannelOut(
        id=cid, name=name, description=body.description,
        created_by=user["id"], created_at=now,
    )


@router.get("/{channel_id}", response_model=ChannelOut, dependencies=[Depends(current_user)])
async def get_channel(channel_id: str, request: Request) -> ChannelOut:
    cur = await _db(request).execute(
        "SELECT id, name, description, created_by, created_at FROM channels WHERE id = ?",
        (channel_id,),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="channel not found")
    return ChannelOut(
        id=row[0], name=row[1], description=row[2], created_by=row[3], created_at=row[4]
    )


@router.delete("/{channel_id}", status_code=204)
async def delete_channel(
    channel_id: str, request: Request, user: dict = Depends(current_user)
) -> None:
    db = _db(request)
    cur = await db.execute(
        "SELECT created_by FROM channels WHERE id = ?", (channel_id,)
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="channel not found")
    if user["role"] != "admin" and row[0] != user["id"]:
        raise HTTPException(status_code=403, detail="only the creator or an admin can delete")
    await db.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
    await db.commit()


@router.get(
    "/{channel_id}/messages",
    response_model=list[MessageOut],
    dependencies=[Depends(current_user)],
)
async def list_messages(
    channel_id: str,
    request: Request,
    before: int | None = Query(default=None, description="return messages with id < before"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[MessageOut]:
    """Most-recent-first page of history (oldest-first within the returned page)."""
    db = _db(request)
    await _require_channel(db, channel_id)
    if before is not None:
        cur = await db.execute(
            "SELECT id, channel_id, user_id, username, content, created_at "
            "FROM channel_messages WHERE channel_id = ? AND id < ? ORDER BY id DESC LIMIT ?",
            (channel_id, before, limit),
        )
    else:
        cur = await db.execute(
            "SELECT id, channel_id, user_id, username, content, created_at "
            "FROM channel_messages WHERE channel_id = ? ORDER BY id DESC LIMIT ?",
            (channel_id, limit),
        )
    rows = list(await cur.fetchall())
    rows.reverse()  # return oldest-first for natural rendering
    return [
        MessageOut(
            id=r[0], channel_id=r[1], user_id=r[2], username=r[3],
            content=r[4], created_at=r[5],
        )
        for r in rows
    ]


@router.post("/{channel_id}/messages", response_model=MessageOut)
async def post_message(
    channel_id: str, body: MessageIn, request: Request, user: dict = Depends(current_user)
) -> MessageOut:
    db = _db(request)
    await _require_channel(db, channel_id)
    msg = await _persist_message(db, channel_id, user, body.content.strip())
    await hub.broadcast(channel_id, {"type": "message", **msg})
    return MessageOut(**msg)


# ---- WebSocket ----

async def _ws_user(db: aiosqlite.Connection, raw: str | None) -> dict | None:
    """Authenticate a WebSocket from the session cookie (mirrors current_user)."""
    payload = read_session(raw)
    if not payload:
        return None
    cur = await db.execute(
        "SELECT id, username, role, token_version FROM users WHERE id = ?",
        (payload["uid"],),
    )
    row = await cur.fetchone()
    if not row or int(payload.get("tv", 0)) != int(row[3]):
        return None
    return {"id": row[0], "username": row[1], "role": row[2]}


@router.websocket("/{channel_id}/ws")
async def channel_ws(channel_id: str, websocket: WebSocket) -> None:
    db: aiosqlite.Connection = websocket.app.state.db
    raw_cookie = websocket.cookies.get(SESSION_COOKIE)
    user = await _ws_user(db, raw_cookie)
    if user is None:
        await websocket.close(code=1008)  # policy violation: not authenticated
        return
    cur = await db.execute("SELECT 1 FROM channels WHERE id = ?", (channel_id,))
    if not await cur.fetchone():
        await websocket.close(code=1008)
        return
    # Cap concurrent sockets per user (coarse DoS backstop).
    if not await hub.try_reserve(user["id"], settings.channel_max_connections_per_user):
        await websocket.close(code=1013)  # try again later
        return

    uid, uname = user["id"], user["username"]
    await websocket.accept()
    await hub.connect(channel_id, websocket)
    await hub.broadcast(
        channel_id,
        {"type": "presence", "event": "join", "username": uname,
         "online": await hub.count(channel_id)},
    )
    interval = settings.channel_ws_revalidate_seconds
    frames: deque[float] = deque()
    last_check = time.time()
    try:
        while True:
            try:
                data: Any = await asyncio.wait_for(
                    websocket.receive_json(), timeout=interval
                )
                got = True
            except asyncio.TimeoutError:
                got = False
            except WebSocketDisconnect:
                break
            except Exception:
                break  # malformed frame / transport error — drop the connection

            # Periodic re-validation: a session revoked AFTER connect
            # (logout-everywhere / password reset / user deletion) must not keep
            # this socket alive. Time-based so a flood can't starve the check.
            now = time.time()
            if now - last_check >= interval:
                last_check = now
                if await _ws_user(db, raw_cookie) is None:
                    break
            if not got:
                continue

            # Rate-limit ALL inbound frames before any DB work / broadcast.
            if not _rate_allow(frames, now, _FRAME_RATE_MAX, _FRAME_RATE_WINDOW):
                continue

            kind = data.get("type") if isinstance(data, dict) else None
            if kind == "message":
                content = str(data.get("content") or "").strip()
                if not content or len(content) > _MAX_CONTENT:
                    continue
                # Re-validate immediately before writing under this identity so a
                # revoked user can't post (the integrity half of revocation).
                fresh = await _ws_user(db, raw_cookie)
                if fresh is None:
                    break
                msg = await _persist_message(db, channel_id, fresh, content)
                await hub.broadcast(channel_id, {"type": "message", **msg})
            elif kind == "typing":
                await hub.broadcast(channel_id, {"type": "typing", "username": uname})
    finally:
        await hub.disconnect(channel_id, websocket)
        await hub.release(uid)
        await hub.broadcast(
            channel_id,
            {"type": "presence", "event": "leave", "username": uname,
             "online": await hub.count(channel_id)},
        )
        try:
            await websocket.close(code=1008)
        except Exception:
            pass
