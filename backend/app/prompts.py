import time

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import current_user

router = APIRouter(
    prefix="/api/prompts",
    tags=["prompts"],
    dependencies=[Depends(current_user)],
)


class PromptIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)


class PromptPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    content: str | None = None


class PromptOut(BaseModel):
    id: int
    title: str
    content: str
    created_at: int
    updated_at: int


def _db(request: Request) -> aiosqlite.Connection:
    return request.app.state.db


@router.get("", response_model=list[PromptOut])
async def list_prompts(request: Request, user: dict = Depends(current_user)):
    db = _db(request)
    cur = await db.execute(
        """
        SELECT id, title, content, created_at, updated_at
        FROM prompts WHERE user_id = ? ORDER BY updated_at DESC
        """,
        (user["id"],),
    )
    rows = await cur.fetchall()
    return [
        PromptOut(id=r[0], title=r[1], content=r[2], created_at=r[3], updated_at=r[4])
        for r in rows
    ]


@router.post("", response_model=PromptOut)
async def create_prompt(
    body: PromptIn, request: Request, user: dict = Depends(current_user)
):
    db = _db(request)
    now = int(time.time())
    pid = await db.insert(
        "INSERT INTO prompts (user_id, title, content, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (user["id"], body.title, body.content, now, now),
    )
    return PromptOut(
        id=pid,
        title=body.title,
        content=body.content,
        created_at=now,
        updated_at=now,
    )


@router.patch("/{pid}", response_model=PromptOut)
async def update_prompt(
    pid: int, body: PromptPatch, request: Request, user: dict = Depends(current_user)
):
    db = _db(request)
    cur = await db.execute(
        "SELECT title, content FROM prompts WHERE id = ? AND user_id = ?",
        (pid, user["id"]),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="prompt not found")
    title = body.title if body.title is not None else row[0]
    content = body.content if body.content is not None else row[1]
    now = int(time.time())
    await db.execute(
        "UPDATE prompts SET title = ?, content = ?, updated_at = ? WHERE id = ?",
        (title, content, now, pid),
    )
    cur2 = await db.execute(
        "SELECT created_at FROM prompts WHERE id = ?", (pid,)
    )
    created_at = (await cur2.fetchone())[0]
    await db.commit()
    return PromptOut(
        id=pid, title=title, content=content, created_at=created_at, updated_at=now
    )


@router.delete("/{pid}", status_code=204)
async def delete_prompt(
    pid: int, request: Request, user: dict = Depends(current_user)
):
    db = _db(request)
    await db.execute(
        "DELETE FROM prompts WHERE id = ? AND user_id = ?", (pid, user["id"])
    )
    await db.commit()
