"""Admin audit trail. record() logs a privileged action; the admin-only list
endpoint surfaces it. Failures to record never break the underlying action."""
import logging
import time

import aiosqlite
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from .auth import require_admin

log = logging.getLogger("free_webui.audit")

router = APIRouter(prefix="/api/admin/audit", tags=["admin"], dependencies=[Depends(require_admin)])


async def record(db: aiosqlite.Connection, actor: dict, action: str, detail: str = "") -> None:
    try:
        await db.execute(
            "INSERT INTO audit_log (user_id, username, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            (actor.get("id"), actor.get("username"), action, detail, int(time.time())),
        )
        await db.commit()
    except Exception:  # auditing must never break the action it records
        log.exception("failed to write audit entry: %s", action)


class AuditEntry(BaseModel):
    id: int
    username: str | None
    action: str
    detail: str | None
    created_at: int


@router.get("", response_model=list[AuditEntry])
async def list_audit(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    before: int | None = Query(default=None),
):
    db = request.app.state.db
    if before:
        cur = await db.execute(
            "SELECT id, username, action, detail, created_at FROM audit_log "
            "WHERE id < ? ORDER BY id DESC LIMIT ?",
            (before, limit),
        )
    else:
        cur = await db.execute(
            "SELECT id, username, action, detail, created_at FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        )
    return [
        AuditEntry(id=r[0], username=r[1], action=r[2], detail=r[3], created_at=r[4])
        for r in await cur.fetchall()
    ]
