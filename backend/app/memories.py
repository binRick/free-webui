"""Per-user memories: short facts the user wants the model to remember
across every conversation. Manually curated for v1 (no auto-extraction).
"""
import time

import aiosqlite
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from .auth import current_user

router = APIRouter(
    prefix="/api/memories",
    tags=["memories"],
    dependencies=[Depends(current_user)],
)


class MemoryIn(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


class MemoryOut(BaseModel):
    id: int
    content: str
    created_at: int
    updated_at: int


def _db(request: Request) -> aiosqlite.Connection:
    return request.app.state.db


@router.get("", response_model=list[MemoryOut])
async def list_memories(request: Request, user: dict = Depends(current_user)):
    db = _db(request)
    cur = await db.execute(
        "SELECT id, content, created_at, updated_at FROM memories WHERE user_id = ? ORDER BY id",
        (user["id"],),
    )
    return [
        MemoryOut(id=r[0], content=r[1], created_at=r[2], updated_at=r[3])
        for r in await cur.fetchall()
    ]


@router.post("", response_model=MemoryOut)
async def create_memory(
    body: MemoryIn, request: Request, user: dict = Depends(current_user)
):
    db = _db(request)
    now = int(time.time())
    mid = await db.insert(
        "INSERT INTO memories (user_id, content, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (user["id"], body.content, now, now),
    )
    await db.commit()
    return MemoryOut(id=mid, content=body.content, created_at=now, updated_at=now)


@router.delete("/{mid}", status_code=204)
async def delete_memory(
    mid: int, request: Request, user: dict = Depends(current_user)
):
    db = _db(request)
    await db.execute(
        "DELETE FROM memories WHERE id = ? AND user_id = ?", (mid, user["id"])
    )
    await db.commit()


async def load_memory_context(
    db: aiosqlite.Connection, user_id: int
) -> str | None:
    cur = await db.execute(
        "SELECT content FROM memories WHERE user_id = ? ORDER BY id", (user_id,)
    )
    rows = await cur.fetchall()
    if not rows:
        return None
    bullets = "\n".join(f"- {r[0]}" for r in rows)
    return (
        "Persistent facts the user wants you to remember across all "
        "conversations:\n" + bullets
    )
