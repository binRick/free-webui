"""Per-model access control.

A model with no `model_access` rows is public (everyone can see/use it). Once a
model has any rows, it is restricted to the listed users + members of the listed
groups. Admins always have access to every model.
"""
from __future__ import annotations

import aiosqlite


async def user_group_ids(db: aiosqlite.Connection, user_id: int) -> set[int]:
    cur = await db.execute(
        "SELECT group_id FROM group_members WHERE user_id = ?", (user_id,)
    )
    return {r[0] for r in await cur.fetchall()}


async def restricted_model_ids(db: aiosqlite.Connection) -> set[str]:
    """Models that have at least one access grant (i.e. are NOT public)."""
    cur = await db.execute("SELECT DISTINCT model_id FROM model_access")
    return {r[0] for r in await cur.fetchall()}


async def can_access_model(db: aiosqlite.Connection, user: dict, model_id: str) -> bool:
    if user.get("role") == "admin":
        return True
    cur = await db.execute(
        "SELECT group_id, user_id FROM model_access WHERE model_id = ?", (model_id,)
    )
    grants = await cur.fetchall()
    if not grants:
        return True  # public
    gids = await user_group_ids(db, user["id"])
    for group_id, uid in grants:
        if uid is not None and uid == user["id"]:
            return True
        if group_id is not None and group_id in gids:
            return True
    return False


async def filter_models(
    db: aiosqlite.Connection, user: dict, model_ids: list[str]
) -> list[str]:
    """Drop models the user may not access. Admins and the all-public case are
    fast-pathed so the common deployment pays nothing."""
    if user.get("role") == "admin":
        return model_ids
    restricted = await restricted_model_ids(db)
    if not restricted:
        return model_ids
    gids = await user_group_ids(db, user["id"])
    cur = await db.execute("SELECT model_id, group_id, user_id FROM model_access")
    allowed: set[str] = set()
    for mid, group_id, uid in await cur.fetchall():
        if (uid is not None and uid == user["id"]) or (group_id is not None and group_id in gids):
            allowed.add(mid)
    return [m for m in model_ids if m not in restricted or m in allowed]
