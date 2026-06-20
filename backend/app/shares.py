"""Public read-only share links for a conversation. The owner endpoints are
auth-scoped; GET /api/shared/{token} is public (no auth) and returns the title,
the active messages, and — so inline [n] citations resolve — a redacted source
list per message: web sources (public URLs) are kept; document sources are
collapsed to a bare marker so the owner's private filename + excerpt never leak."""
import json
import secrets
import time

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from .auth import current_user
from .config import settings
from .conversations import _decode_content
from .files import _INLINE_BUDGET, expand_file_refs
from .permissions import require_permission

router = APIRouter(prefix="/api", tags=["shares"])


def _db(request: Request) -> aiosqlite.Connection:
    return request.app.state.db


def _public_source(s: dict) -> dict:
    """Reduce a stored source for the PUBLIC share payload. Web sources are
    public URLs — kept. Document sources would otherwise disclose the owner's
    private filename + a verbatim excerpt, so collapse them to a bare marker
    (the inline [n] still resolves to a chip; nothing private is revealed)."""
    if s.get("kind") == "web":
        return s
    return {"kind": "document", "label": "attached document"}


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


@router.post(
    "/conversations/{cid}/share",
    dependencies=[Depends(require_permission("chat_share"))],
)
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
        "SELECT role, content, sources FROM messages WHERE conversation_id = ? AND active = 1 ORDER BY id",
        (cid,),
    )
    # The public viewer is unauthenticated and cannot hit /api/files/{id}, so
    # inline any externalized image bytes back into the payload.
    messages = []
    budget = [_INLINE_BUDGET]
    for r in await cur.fetchall():
        content = await expand_file_refs(db, _decode_content(r[1]), cid, budget)
        msg: dict = {"role": r[0], "content": content}
        if r[2]:  # citation sources (redacted), so inline [n] markers resolve too
            try:
                parsed = json.loads(r[2])
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, list):
                msg["sources"] = [_public_source(s) for s in parsed if isinstance(s, dict)]
        messages.append(msg)
    return {"title": conv[0], "messages": messages}
