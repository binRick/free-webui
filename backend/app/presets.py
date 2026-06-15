import json
import time

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import current_user

router = APIRouter(
    prefix="/api/presets",
    tags=["presets"],
    dependencies=[Depends(current_user)],
)


class PresetIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    model: str | None = None
    system_prompt: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    stop: list[str] | None = None
    description: str | None = Field(default=None, max_length=500)
    tools_enabled: bool = False
    web_search: bool = False
    # Knowledge bundled by this preset (custom assistant) — reusable collections
    # attached to the conversation when the preset is applied.
    collection_ids: list[int] = Field(default_factory=list)


class PresetOut(BaseModel):
    id: int
    name: str
    model: str | None
    system_prompt: str | None
    temperature: float | None
    top_p: float | None
    stop: list[str] | None
    description: str | None = None
    tools_enabled: bool = False
    web_search: bool = False
    collection_ids: list[int] = Field(default_factory=list)
    created_at: int
    updated_at: int


def _db(request: Request) -> aiosqlite.Connection:
    return request.app.state.db


async def _owned_collection_ids(
    db: aiosqlite.Connection, user_id: int, wanted: list[int]
) -> list[int]:
    """Filter `wanted` to the collections the user actually owns, preserving
    order and de-duplicating — a preset must never bundle someone else's KB."""
    wanted = list(dict.fromkeys(wanted))
    if not wanted:
        return []
    placeholders = ",".join("?" * len(wanted))
    cur = await db.execute(
        f"SELECT id FROM collections WHERE user_id = ? AND id IN ({placeholders})",  # noqa: S608
        [user_id, *wanted],
    )
    present = {r[0] for r in await cur.fetchall()}
    return [i for i in wanted if i in present]


async def _preset_collection_ids(db: aiosqlite.Connection, preset_id: int) -> list[int]:
    cur = await db.execute(
        "SELECT collection_id FROM preset_collections WHERE preset_id = ? ORDER BY collection_id",
        (preset_id,),
    )
    return [r[0] for r in await cur.fetchall()]


async def _set_preset_collections(
    db: aiosqlite.Connection, preset_id: int, collection_ids: list[int]
) -> None:
    await db.execute("DELETE FROM preset_collections WHERE preset_id = ?", (preset_id,))
    for cid in collection_ids:
        await db.execute(
            "INSERT INTO preset_collections (preset_id, collection_id) VALUES (?, ?)",
            (preset_id, cid),
        )


def _row_to_out(r, collection_ids: list[int]) -> PresetOut:
    return PresetOut(
        id=r[0],
        name=r[1],
        model=r[2],
        system_prompt=r[3],
        temperature=r[4],
        top_p=r[5],
        stop=json.loads(r[6]) if r[6] else None,
        created_at=r[7],
        updated_at=r[8],
        description=r[9],
        tools_enabled=bool(r[10]),
        web_search=bool(r[11]),
        collection_ids=collection_ids,
    )


@router.get("", response_model=list[PresetOut])
async def list_presets(request: Request, user: dict = Depends(current_user)):
    db = _db(request)
    cur = await db.execute(
        """
        SELECT id, name, model, system_prompt, temperature, top_p, stop,
               created_at, updated_at, description, tools_enabled, web_search
        FROM presets WHERE user_id = ? ORDER BY updated_at DESC
        """,
        (user["id"],),
    )
    rows = await cur.fetchall()
    # One grouped query for all this user's preset→collection links.
    cur = await db.execute(
        """
        SELECT pc.preset_id, pc.collection_id
        FROM preset_collections pc
        JOIN presets p ON p.id = pc.preset_id
        WHERE p.user_id = ?
        ORDER BY pc.collection_id
        """,
        (user["id"],),
    )
    links: dict[int, list[int]] = {}
    for pid, coll_id in await cur.fetchall():
        links.setdefault(pid, []).append(coll_id)
    return [_row_to_out(r, links.get(r[0], [])) for r in rows]


@router.post("", response_model=PresetOut)
async def create_preset(
    body: PresetIn, request: Request, user: dict = Depends(current_user)
):
    db = _db(request)
    now = int(time.time())
    stop_str = json.dumps(body.stop) if body.stop else None
    cur = await db.execute(
        """
        INSERT INTO presets
        (user_id, name, model, system_prompt, temperature, top_p, stop,
         description, tools_enabled, web_search, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user["id"], body.name, body.model, body.system_prompt,
            body.temperature, body.top_p, stop_str, body.description,
            int(body.tools_enabled), int(body.web_search), now, now,
        ),
    )
    pid = cur.lastrowid
    owned = await _owned_collection_ids(db, user["id"], body.collection_ids)
    await _set_preset_collections(db, pid, owned)
    await db.commit()
    return PresetOut(
        id=pid,
        name=body.name,
        model=body.model,
        system_prompt=body.system_prompt,
        temperature=body.temperature,
        top_p=body.top_p,
        stop=body.stop,
        description=body.description,
        tools_enabled=body.tools_enabled,
        web_search=body.web_search,
        collection_ids=owned,
        created_at=now,
        updated_at=now,
    )


@router.put("/{pid}", response_model=PresetOut)
async def update_preset(
    pid: int, body: PresetIn, request: Request, user: dict = Depends(current_user)
):
    """Replace a preset (custom assistant) in place — name, mode, and knowledge."""
    db = _db(request)
    cur = await db.execute(
        "SELECT created_at FROM presets WHERE id = ? AND user_id = ?", (pid, user["id"])
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="preset not found")
    now = int(time.time())
    stop_str = json.dumps(body.stop) if body.stop else None
    await db.execute(
        """
        UPDATE presets SET name = ?, model = ?, system_prompt = ?, temperature = ?,
               top_p = ?, stop = ?, description = ?, tools_enabled = ?,
               web_search = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            body.name, body.model, body.system_prompt, body.temperature, body.top_p,
            stop_str, body.description, int(body.tools_enabled),
            int(body.web_search), now, pid,
        ),
    )
    owned = await _owned_collection_ids(db, user["id"], body.collection_ids)
    await _set_preset_collections(db, pid, owned)
    await db.commit()
    return PresetOut(
        id=pid, name=body.name, model=body.model, system_prompt=body.system_prompt,
        temperature=body.temperature, top_p=body.top_p, stop=body.stop,
        description=body.description, tools_enabled=body.tools_enabled,
        web_search=body.web_search, collection_ids=owned,
        created_at=row[0], updated_at=now,
    )


@router.delete("/{pid}", status_code=204)
async def delete_preset(
    pid: int, request: Request, user: dict = Depends(current_user)
):
    db = _db(request)
    # preset_collections rows cascade via FK ON DELETE CASCADE.
    await db.execute(
        "DELETE FROM presets WHERE id = ? AND user_id = ?", (pid, user["id"])
    )
    await db.commit()
