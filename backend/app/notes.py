"""Notes: a lightweight per-user markdown notebook (a workspace surface distinct
from chats and from reusable prompts). All endpoints are user-scoped."""
import time

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import current_user

router = APIRouter(
    prefix="/api/notes",
    tags=["notes"],
    dependencies=[Depends(current_user)],
)


class NoteIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = ""


class NotePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = None


class NoteOut(BaseModel):
    id: int
    title: str
    content: str
    created_at: int
    updated_at: int


def _db(request: Request) -> aiosqlite.Connection:
    return request.app.state.db


@router.get("", response_model=list[NoteOut])
async def list_notes(request: Request, user: dict = Depends(current_user)):
    db = _db(request)
    cur = await db.execute(
        "SELECT id, title, content, created_at, updated_at FROM notes "
        "WHERE user_id = ? ORDER BY updated_at DESC",
        (user["id"],),
    )
    return [
        NoteOut(id=r[0], title=r[1], content=r[2], created_at=r[3], updated_at=r[4])
        for r in await cur.fetchall()
    ]


@router.post("", response_model=NoteOut)
async def create_note(
    body: NoteIn, request: Request, user: dict = Depends(current_user)
):
    db = _db(request)
    now = int(time.time())
    cur = await db.execute(
        "INSERT INTO notes (user_id, title, content, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (user["id"], body.title.strip(), body.content, now, now),
    )
    await db.commit()
    return NoteOut(
        id=cur.lastrowid, title=body.title.strip(), content=body.content,
        created_at=now, updated_at=now,
    )


async def _owned_note(db: aiosqlite.Connection, nid: int, user_id: int) -> tuple:
    cur = await db.execute(
        "SELECT title, content, created_at FROM notes WHERE id = ? AND user_id = ?",
        (nid, user_id),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="note not found")
    return row


@router.get("/{nid}", response_model=NoteOut)
async def get_note(nid: int, request: Request, user: dict = Depends(current_user)):
    db = _db(request)
    row = await _owned_note(db, nid, user["id"])
    return NoteOut(id=nid, title=row[0], content=row[1], created_at=row[2], updated_at=row[2])


@router.patch("/{nid}", response_model=NoteOut)
async def update_note(
    nid: int, body: NotePatch, request: Request, user: dict = Depends(current_user)
):
    db = _db(request)
    row = await _owned_note(db, nid, user["id"])
    title = body.title.strip() if body.title is not None else row[0]
    content = body.content if body.content is not None else row[1]
    now = int(time.time())
    await db.execute(
        "UPDATE notes SET title = ?, content = ?, updated_at = ? WHERE id = ?",
        (title, content, now, nid),
    )
    await db.commit()
    return NoteOut(id=nid, title=title, content=content, created_at=row[2], updated_at=now)


@router.delete("/{nid}", status_code=204)
async def delete_note(nid: int, request: Request, user: dict = Depends(current_user)):
    db = _db(request)
    await _owned_note(db, nid, user["id"])
    await db.execute("DELETE FROM notes WHERE id = ?", (nid,))
    await db.commit()
