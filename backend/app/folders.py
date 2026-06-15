"""Conversation folders: single-home organization, complementary to tags.

A conversation may belong to at most one folder (``conversations.folder_id``).
Deleting a folder un-files its conversations (folder_id -> NULL) rather than
deleting them. All endpoints are user-scoped."""
import time

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import current_user

router = APIRouter(
    prefix="/api/folders",
    tags=["folders"],
    dependencies=[Depends(current_user)],
)


class FolderIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class FolderOut(BaseModel):
    id: int
    name: str
    created_at: int
    updated_at: int


def _db(request: Request) -> aiosqlite.Connection:
    return request.app.state.db


@router.get("", response_model=list[FolderOut])
async def list_folders(request: Request, user: dict = Depends(current_user)):
    db = _db(request)
    cur = await db.execute(
        "SELECT id, name, created_at, updated_at FROM folders "
        "WHERE user_id = ? ORDER BY name COLLATE NOCASE",
        (user["id"],),
    )
    return [
        FolderOut(id=r[0], name=r[1], created_at=r[2], updated_at=r[3])
        for r in await cur.fetchall()
    ]


@router.post("", response_model=FolderOut)
async def create_folder(
    body: FolderIn, request: Request, user: dict = Depends(current_user)
):
    db = _db(request)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be empty")
    now = int(time.time())
    cur = await db.execute(
        "INSERT INTO folders (user_id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (user["id"], name, now, now),
    )
    await db.commit()
    return FolderOut(id=cur.lastrowid, name=name, created_at=now, updated_at=now)


@router.patch("/{folder_id}", response_model=FolderOut)
async def rename_folder(
    folder_id: int, body: FolderIn, request: Request, user: dict = Depends(current_user)
):
    db = _db(request)
    cur = await db.execute(
        "SELECT created_at FROM folders WHERE id = ? AND user_id = ?",
        (folder_id, user["id"]),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="folder not found")
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be empty")
    now = int(time.time())
    await db.execute(
        "UPDATE folders SET name = ?, updated_at = ? WHERE id = ?", (name, now, folder_id)
    )
    await db.commit()
    return FolderOut(id=folder_id, name=name, created_at=row[0], updated_at=now)


@router.delete("/{folder_id}", status_code=204)
async def delete_folder(
    folder_id: int, request: Request, user: dict = Depends(current_user)
):
    db = _db(request)
    cur = await db.execute(
        "SELECT 1 FROM folders WHERE id = ? AND user_id = ?", (folder_id, user["id"])
    )
    if not await cur.fetchone():
        raise HTTPException(status_code=404, detail="folder not found")
    # Un-file the folder's conversations rather than deleting them.
    await db.execute(
        "UPDATE conversations SET folder_id = NULL WHERE folder_id = ? AND user_id = ?",
        (folder_id, user["id"]),
    )
    await db.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
    await db.commit()
