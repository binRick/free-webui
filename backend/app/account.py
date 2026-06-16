"""Self-service account: a full data export (GDPR-style) and account deletion.

Both act ONLY on the authenticated user's own data — no admin rights needed and
no other user is touched. Export bundles everything the user authored into one
JSON download; deletion re-authenticates with the password, then removes the
user (FK cascades clear their conversations/messages/prompts/etc.) and reclaims
any S3 objects the cascade can't reach.
"""
import json
import time

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from .auth import SESSION_COOKIE, current_user, verify_password
from .files import collect_user_objects, purge_objects

router = APIRouter(prefix="/api/account", tags=["account"])


def _db(request: Request) -> aiosqlite.Connection:
    return request.app.state.db


async def _rows(db, sql: str, params: tuple, cols: list[str]) -> list[dict]:
    """Run a query and zip each row into a dict with the given column names
    (explicit columns keep the export portable across SQLite + Postgres rather
    than relying on cursor.description)."""
    cur = await db.execute(sql, params)
    return [dict(zip(cols, row)) for row in await cur.fetchall()]


@router.get("/export")
async def export_account(request: Request, user: dict = Depends(current_user)) -> Response:
    """Download everything this user authored as one JSON document."""
    db = _db(request)
    uid = user["id"]

    profile = await _rows(
        db, "SELECT id, username, role, created_at FROM users WHERE id = ?",
        (uid,), ["id", "username", "role", "created_at"],
    )
    conversations = await _rows(
        db,
        "SELECT id, title, model, system_prompt, pinned, archived, folder_id, created_at, updated_at "
        "FROM conversations WHERE user_id = ? ORDER BY id",
        (uid,),
        ["id", "title", "model", "system_prompt", "pinned", "archived", "folder_id", "created_at", "updated_at"],
    )
    # All messages (incl. superseded variants) in this user's conversations.
    messages = await _rows(
        db,
        "SELECT m.id, m.conversation_id, m.role, m.content, m.model, m.active, m.created_at "
        "FROM messages m JOIN conversations c ON c.id = m.conversation_id "
        "WHERE c.user_id = ? ORDER BY m.id",
        (uid,),
        ["id", "conversation_id", "role", "content", "model", "active", "created_at"],
    )
    prompts = await _rows(
        db, "SELECT id, title, content, created_at, updated_at FROM prompts WHERE user_id = ? ORDER BY id",
        (uid,), ["id", "title", "content", "created_at", "updated_at"],
    )
    notes = await _rows(
        db, "SELECT id, title, content, created_at, updated_at FROM notes WHERE user_id = ? ORDER BY id",
        (uid,), ["id", "title", "content", "created_at", "updated_at"],
    )
    memories = await _rows(
        db, "SELECT id, content, created_at, updated_at FROM memories WHERE user_id = ? ORDER BY id",
        (uid,), ["id", "content", "created_at", "updated_at"],
    )
    presets = await _rows(
        db,
        "SELECT id, name, model, system_prompt, description, created_at, updated_at "
        "FROM presets WHERE user_id = ? ORDER BY id",
        (uid,),
        ["id", "name", "model", "system_prompt", "description", "created_at", "updated_at"],
    )
    folders = await _rows(
        db, "SELECT id, name, created_at, updated_at FROM folders WHERE user_id = ? ORDER BY id",
        (uid,), ["id", "name", "created_at", "updated_at"],
    )
    collections = await _rows(
        db, "SELECT id, name, created_at, updated_at FROM collections WHERE user_id = ? ORDER BY id",
        (uid,), ["id", "name", "created_at", "updated_at"],
    )
    # API keys: metadata only — never the secret or its hash.
    api_keys = await _rows(
        db, "SELECT id, name, key_prefix, last_used_at, created_at FROM api_keys WHERE user_id = ? ORDER BY id",
        (uid,), ["id", "name", "key_prefix", "last_used_at", "created_at"],
    )
    feedback = await _rows(
        db, "SELECT id, message_id, rating, comment, created_at FROM message_feedback WHERE user_id = ? ORDER BY id",
        (uid,), ["id", "message_id", "rating", "comment", "created_at"],
    )

    data = {
        "exported_at": int(time.time()),
        "profile": profile[0] if profile else None,
        "conversations": conversations,
        "messages": messages,
        "prompts": prompts,
        "notes": notes,
        "memories": memories,
        "presets": presets,
        "folders": folders,
        "collections": collections,
        "api_keys": api_keys,
        "feedback": feedback,
    }
    body = json.dumps(data, indent=2, ensure_ascii=False, default=str).encode("utf-8")
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="free-webui-account-export.json"'},
    )


class DeleteAccountBody(BaseModel):
    password: str


@router.delete("")
async def delete_account(
    body: DeleteAccountBody,
    request: Request,
    response: Response,
    user: dict = Depends(current_user),
) -> dict:
    """Permanently delete the caller's own account and all their data."""
    db = _db(request)
    uid = user["id"]
    cur = await db.execute("SELECT password_hash, role FROM users WHERE id = ?", (uid,))
    row = await cur.fetchone()
    if not row or not verify_password(row[0], body.password):
        raise HTTPException(status_code=401, detail="password incorrect")
    # Don't let the last admin orphan the instance (no one could administer it).
    if row[1] == "admin":
        cur = await db.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'")
        if (await cur.fetchone())[0] <= 1:
            raise HTTPException(
                status_code=400, detail="cannot delete the only remaining admin account"
            )
    # Reclaim S3 objects before the cascade destroys the index rows (no-op on DB
    # storage), then purge after the delete commits.
    stale = await collect_user_objects(db, uid)
    await db.execute("DELETE FROM users WHERE id = ?", (uid,))
    await db.commit()
    await purge_objects(stale)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"deleted": True}
