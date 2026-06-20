"""Admin API for the per-feature permission matrix.

Exposes the permission registry, the global defaults, and the per-group grants,
plus the writes to change them. Enforcement lives in permissions.py; this is just
the management surface. Admin-only.
"""
import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .audit import record
from .auth import require_admin
from .permissions import PERMISSION_KEYS, PERMISSIONS, get_defaults, group_grants

router = APIRouter(
    prefix="/api/admin/permissions",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


def _db(request: Request) -> aiosqlite.Connection:
    return request.app.state.db


class DefaultsIn(BaseModel):
    # key -> allowed. Unknown keys are ignored; omitted keys are left unchanged.
    defaults: dict[str, bool]


class GroupPermsIn(BaseModel):
    # the full set of keys this group grants (replaces the group's current set).
    keys: list[str]


@router.get("")
async def get_matrix(request: Request) -> dict:
    """The whole matrix: the key registry (with labels + built-in defaults), the
    current global defaults, and each group's granted keys."""
    db = _db(request)
    grants = await group_grants(db)
    cur = await db.execute("SELECT id, name FROM groups ORDER BY name")
    groups = [
        {"id": gid, "name": name, "keys": sorted(grants.get(gid, []))}
        for gid, name in await cur.fetchall()
    ]
    return {
        "permissions": [
            {"key": k, "label": PERMISSIONS[k][0], "builtin_default": PERMISSIONS[k][1]}
            for k in PERMISSION_KEYS
        ],
        "defaults": await get_defaults(db),
        "groups": groups,
    }


@router.put("/defaults")
async def set_defaults(body: DefaultsIn, request: Request, me: dict = Depends(require_admin)) -> dict:
    db = _db(request)
    applied = {k: v for k, v in body.defaults.items() if k in PERMISSIONS}
    if not applied and body.defaults:
        raise HTTPException(status_code=422, detail="no known permission keys supplied")
    # All-or-nothing: a mid-loop failure must not leave some keys flipped and
    # others not (a partial write could silently revert a DENY to ALLOW on the
    # shared connection). record() commits on its own, so it stays outside.
    async with db.transaction():
        for key, allowed in applied.items():
            # Upsert: portable across SQLite + Postgres without ON CONFLICT dialects.
            await db.execute("DELETE FROM permission_defaults WHERE key = ?", (key,))
            await db.execute(
                "INSERT INTO permission_defaults (key, allowed) VALUES (?, ?)",
                (key, 1 if allowed else 0),
            )
    await record(db, me, "permissions.defaults", str(applied))
    return await get_defaults(db)


@router.put("/groups/{gid}")
async def set_group_permissions(
    gid: int, body: GroupPermsIn, request: Request, me: dict = Depends(require_admin)
) -> dict:
    db = _db(request)
    cur = await db.execute("SELECT 1 FROM groups WHERE id = ?", (gid,))
    if not await cur.fetchone():
        raise HTTPException(status_code=404, detail="group not found")
    keys = sorted({k for k in body.keys if k in PERMISSIONS})
    # Replace the group's grant set atomically (delete + re-insert as one unit).
    async with db.transaction():
        await db.execute("DELETE FROM group_permissions WHERE group_id = ?", (gid,))
        for key in keys:
            await db.execute(
                "INSERT INTO group_permissions (group_id, key, allowed) VALUES (?, ?, 1)",
                (gid, key),
            )
    await record(db, me, "permissions.group", f"gid={gid} keys={keys}")
    return {"id": gid, "keys": keys}
