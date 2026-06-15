"""Real-time chat channels: shared multi-user rooms with live WebSocket delivery.

Workspace-wide — every authenticated user can list, read, and post. REST handles
CRUD + history + posting (persist then broadcast); a per-channel WebSocket pushes
live ``message``/``typing``/``presence`` frames and also accepts inbound
``message``/``typing`` for true real-time posting.

The broadcast hub is in-process (a module-level singleton). That fits the
single-process design; horizontal scaling would need an external pub/sub (Redis).
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
from .config import settings

router = APIRouter(prefix="/api/channels", tags=["channels"])

_MAX_CONTENT = 4000
# Per-connection inbound frame budget (covers message + typing) — bounds DB
# writes / broadcasts a single socket can drive.
_FRAME_RATE_MAX = 20
_FRAME_RATE_WINDOW = 10.0


class ChannelHub:
    """Tracks live WebSocket subscribers per channel and fans messages out."""

    def __init__(self) -> None:
        self._channels: dict[str, set[WebSocket]] = {}
        self._user_counts: dict[int, int] = {}
        self._lock = asyncio.Lock()

    async def try_reserve(self, user_id: int, cap: int) -> bool:
        """Reserve a connection slot for a user, capping concurrent sockets."""
        async with self._lock:
            if self._user_counts.get(user_id, 0) >= cap:
                return False
            self._user_counts[user_id] = self._user_counts.get(user_id, 0) + 1
            return True

    async def release(self, user_id: int) -> None:
        async with self._lock:
            n = self._user_counts.get(user_id, 0) - 1
            if n <= 0:
                self._user_counts.pop(user_id, None)
            else:
                self._user_counts[user_id] = n

    async def connect(self, channel_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._channels.setdefault(channel_id, set()).add(ws)

    async def disconnect(self, channel_id: str, ws: WebSocket) -> None:
        async with self._lock:
            conns = self._channels.get(channel_id)
            if conns:
                conns.discard(ws)
                if not conns:
                    self._channels.pop(channel_id, None)

    async def count(self, channel_id: str) -> int:
        async with self._lock:
            return len(self._channels.get(channel_id, ()))

    async def broadcast(self, channel_id: str, message: dict) -> None:
        # Snapshot under the lock, send outside it so a slow/dead socket can't
        # block the whole fan-out or deadlock against (dis)connect.
        async with self._lock:
            conns = list(self._channels.get(channel_id, ()))
        dead: list[WebSocket] = []
        for ws in conns:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(channel_id, ws)


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
    cur = await db.execute(
        "INSERT INTO channel_messages (channel_id, user_id, username, content, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (channel_id, user["id"], user["username"], content, ts),
    )
    await db.commit()
    return {
        "id": cur.lastrowid,
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
