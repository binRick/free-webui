"""Public read-only share links for a conversation. The owner endpoints are
auth-scoped; GET /api/shared/{token} is public (no auth) and returns only the
title + messages — no params, no user info."""
import secrets
import time

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from .auth import current_user
from .config import settings
from .conversations import _decode_content
from .files import expand_file_refs

router = APIRouter(prefix="/api", tags=["shares"])


def _db(request: Request) -> aiosqlite.Connection:
    return request.app.state.db


async def _owned(db: aiosqlite.Connection, cid: str, user_id: int) -> None:
    cur = await db.execute(
        "SELECT 1 FROM conversations WHERE id = ? AND user_id = ?", (cid, user_id)
    )
    if not await cur.fetchone():
        raise HTTPException(status_code=404, detail="conversation not found")


@router.get("/conversations/{cid}/share")
async def get_share(cid: str, request: Request, user: dict = Depends(current_user)) -> dict:
    db = _db(request)
    await _owned(db, cid, user["id"])
    cur = await db.execute("SELECT token FROM shares WHERE conversation_id = ?", (cid,))
    row = await cur.fetchone()
    return {"token": row[0] if row else None}


@router.post("/conversations/{cid}/share")
async def create_share(cid: str, request: Request, user: dict = Depends(current_user)) -> dict:
    if not settings.allow_public_sharing:
        raise HTTPException(status_code=403, detail="public sharing is disabled")
    db = _db(request)
    await _owned(db, cid, user["id"])
    cur = await db.execute("SELECT token FROM shares WHERE conversation_id = ?", (cid,))
    row = await cur.fetchone()
    if row:
        return {"token": row[0]}
    token = secrets.token_urlsafe(16)
    await db.execute(
        "INSERT INTO shares (token, conversation_id, user_id, created_at) VALUES (?, ?, ?, ?)",
        (token, cid, user["id"], int(time.time())),
    )
    await db.commit()
    return {"token": token}


@router.delete("/conversations/{cid}/share", status_code=204)
async def delete_share(cid: str, request: Request, user: dict = Depends(current_user)) -> None:
    db = _db(request)
    await _owned(db, cid, user["id"])
    await db.execute(
        "DELETE FROM shares WHERE conversation_id = ? AND user_id = ?", (cid, user["id"])
    )
    await db.commit()


@router.get("/shared/{token}")
async def get_shared(token: str, request: Request) -> dict:
    """Public: the shared conversation's title + active messages. No auth."""
    if not settings.allow_public_sharing:
        raise HTTPException(status_code=404, detail="not found")
    db = _db(request)
    cur = await db.execute("SELECT conversation_id FROM shares WHERE token = ?", (token,))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    cid = row[0]
    cur = await db.execute("SELECT title FROM conversations WHERE id = ?", (cid,))
    conv = await cur.fetchone()
    if not conv:
        raise HTTPException(status_code=404, detail="not found")
    cur = await db.execute(
        "SELECT role, content FROM messages WHERE conversation_id = ? AND active = 1 ORDER BY id",
        (cid,),
    )
    # The public viewer is unauthenticated and cannot hit /api/files/{id}, so
    # inline any externalized image bytes back into the payload.
    messages = []
    for r in await cur.fetchall():
        content = await expand_file_refs(db, _decode_content(r[1]), cid)
        messages.append({"role": r[0], "content": content})
    return {"title": conv[0], "messages": messages}
