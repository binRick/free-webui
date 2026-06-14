import json
import time

import aiosqlite
from fastapi import APIRouter, Depends, Request
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


class PresetOut(BaseModel):
    id: int
    name: str
    model: str | None
    system_prompt: str | None
    temperature: float | None
    top_p: float | None
    stop: list[str] | None
    created_at: int
    updated_at: int


def _db(request: Request) -> aiosqlite.Connection:
    return request.app.state.db


def _row_to_out(r) -> PresetOut:
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
    )


@router.get("", response_model=list[PresetOut])
async def list_presets(request: Request, user: dict = Depends(current_user)):
    db = _db(request)
    cur = await db.execute(
        """
        SELECT id, name, model, system_prompt, temperature, top_p, stop,
               created_at, updated_at
        FROM presets WHERE user_id = ? ORDER BY updated_at DESC
        """,
        (user["id"],),
    )
    return [_row_to_out(r) for r in await cur.fetchall()]


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
        (user_id, name, model, system_prompt, temperature, top_p, stop, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user["id"], body.name, body.model, body.system_prompt,
            body.temperature, body.top_p, stop_str, now, now,
        ),
    )
    await db.commit()
    return PresetOut(
        id=cur.lastrowid,
        name=body.name,
        model=body.model,
        system_prompt=body.system_prompt,
        temperature=body.temperature,
        top_p=body.top_p,
        stop=body.stop,
        created_at=now,
        updated_at=now,
    )


@router.delete("/{pid}", status_code=204)
async def delete_preset(
    pid: int, request: Request, user: dict = Depends(current_user)
):
    db = _db(request)
    await db.execute(
        "DELETE FROM presets WHERE id = ? AND user_id = ?", (pid, user["id"])
    )
    await db.commit()
