"""Admin API for user groups and per-model access control.

Groups bundle users; model_access grants make a model visible only to listed
users/groups (a model with no grants is public). See access.py for enforcement.
"""
import time

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .audit import record
from .auth import require_admin

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


def _db(request: Request) -> aiosqlite.Connection:
    return request.app.state.db


async def _existing_ids(db: aiosqlite.Connection, table: str, ids) -> list[int]:
    """Subset of `ids` that exist in `table` (preserving order). Used to skip
    unknown ids — SQLite's OR IGNORE does NOT suppress FK violations, so we
    must validate before inserting. `table` is a fixed literal, never input."""
    ordered = list(dict.fromkeys(ids))
    if not ordered:
        return []
    placeholders = ",".join("?" * len(ordered))
    cur = await db.execute(
        f"SELECT id FROM {table} WHERE id IN ({placeholders})", ordered  # noqa: S608
    )
    present = {r[0] for r in await cur.fetchall()}
    return [i for i in ordered if i in present]


# ---- groups ----

class GroupOut(BaseModel):
    id: int
    name: str
    member_count: int
    created_at: int


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class MembersIn(BaseModel):
    user_ids: list[int]


@router.get("/groups", response_model=list[GroupOut])
async def list_groups(request: Request):
    db = _db(request)
    cur = await db.execute(
        """
        SELECT g.id, g.name, g.created_at, COUNT(m.user_id)
        FROM groups g LEFT JOIN group_members m ON m.group_id = g.id
        GROUP BY g.id, g.name, g.created_at
        ORDER BY g.name
        """
    )
    return [
        GroupOut(id=r[0], name=r[1], member_count=r[3], created_at=r[2])
        for r in await cur.fetchall()
    ]


@router.post("/groups", response_model=GroupOut)
async def create_group(body: GroupIn, request: Request, me: dict = Depends(require_admin)):
    db = _db(request)
    cur = await db.execute("SELECT 1 FROM groups WHERE name = ?", (body.name,))
    if await cur.fetchone():
        raise HTTPException(status_code=409, detail="group name already exists")
    now = int(time.time())
    gid = await db.insert(
        "INSERT INTO groups (name, created_at) VALUES (?, ?)", (body.name, now)
    )
    await db.commit()
    await record(db, me, "group.create", f"name={body.name}")
    return GroupOut(id=gid, name=body.name, member_count=0, created_at=now)


@router.delete("/groups/{gid}", status_code=204)
async def delete_group(gid: int, request: Request, me: dict = Depends(require_admin)):
    db = _db(request)
    await db.execute("DELETE FROM groups WHERE id = ?", (gid,))
    await db.commit()
    await record(db, me, "group.delete", f"gid={gid}")


@router.get("/groups/{gid}/members")
async def list_members(gid: int, request: Request) -> dict:
    db = _db(request)
    cur = await db.execute("SELECT 1 FROM groups WHERE id = ?", (gid,))
    if not await cur.fetchone():
        raise HTTPException(status_code=404, detail="group not found")
    cur = await db.execute(
        "SELECT user_id FROM group_members WHERE group_id = ?", (gid,)
    )
    return {"user_ids": [r[0] for r in await cur.fetchall()]}


@router.put("/groups/{gid}/members")
async def set_members(gid: int, body: MembersIn, request: Request) -> dict:
    db = _db(request)
    cur = await db.execute("SELECT 1 FROM groups WHERE id = ?", (gid,))
    if not await cur.fetchone():
        raise HTTPException(status_code=404, detail="group not found")
    await db.execute("DELETE FROM group_members WHERE group_id = ?", (gid,))
    for uid in await _existing_ids(db, "users", body.user_ids):
        await db.execute(
            "INSERT INTO group_members (group_id, user_id) VALUES (?, ?)", (gid, uid)
        )
    await db.commit()
    cur = await db.execute(
        "SELECT user_id FROM group_members WHERE group_id = ?", (gid,)
    )
    return {"user_ids": [r[0] for r in await cur.fetchall()]}


# ---- model access ----

class ModelAccessIn(BaseModel):
    # model ids can contain '/', so carry the id in the body, not the path.
    model_id: str = Field(min_length=1, max_length=200)
    group_ids: list[int] = []
    user_ids: list[int] = []


@router.get("/model_access")
async def list_model_access(request: Request) -> dict:
    """All grants keyed by model id. A model absent here is public."""
    db = _db(request)
    cur = await db.execute("SELECT model_id, group_id, user_id FROM model_access")
    out: dict[str, dict] = {}
    for mid, gid, uid in await cur.fetchall():
        entry = out.setdefault(mid, {"group_ids": [], "user_ids": []})
        if gid is not None:
            entry["group_ids"].append(gid)
        if uid is not None:
            entry["user_ids"].append(uid)
    return out


@router.put("/model_access")
async def set_model_access(body: ModelAccessIn, request: Request, me: dict = Depends(require_admin)) -> dict:
    """Replace the grants for a model. Empty group_ids + user_ids makes it
    public again (all grant rows removed)."""
    db = _db(request)
    # Resolve ids BEFORE mutating. Fail closed: if the caller asked to restrict
    # (supplied ids) but none resolve, refuse rather than silently leaving the
    # model public (which would be a fail-open access bug).
    groups = await _existing_ids(db, "groups", body.group_ids)
    users = await _existing_ids(db, "users", body.user_ids)
    if (body.group_ids or body.user_ids) and not groups and not users:
        raise HTTPException(
            status_code=422,
            detail="none of the supplied group_ids/user_ids exist",
        )
    await db.execute("DELETE FROM model_access WHERE model_id = ?", (body.model_id,))
    for gid in groups:
        await db.execute(
            "INSERT INTO model_access (model_id, group_id) VALUES (?, ?)",
            (body.model_id, gid),
        )
    for uid in users:
        await db.execute(
            "INSERT INTO model_access (model_id, user_id) VALUES (?, ?)",
            (body.model_id, uid),
        )
    await db.commit()
    public = not groups and not users
    await record(
        db, me, "model_access.set",
        f"model={body.model_id} {'public' if public else f'groups={groups} users={users}'}",
    )
    return {"model_id": body.model_id, "group_ids": groups, "user_ids": users}
