"""First-class tag management across a user's conversations.

Per-conversation tag editing lives in conversations.py (GET/PUT /{cid}/tags);
this router is the cross-conversation view: list every tag the user uses (with
counts), rename a tag everywhere (merging into an existing one), and delete a
tag from every conversation. All endpoints are user-scoped."""
import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import current_user

router = APIRouter(prefix="/api/tags", tags=["tags"], dependencies=[Depends(current_user)])


def _db(request: Request) -> aiosqlite.Connection:
    return request.app.state.db


class TagCount(BaseModel):
    tag: str
    count: int


class RenameBody(BaseModel):
    old: str = Field(min_length=1, max_length=40)
    new: str = Field(min_length=1, max_length=40)


@router.get("", response_model=list[TagCount])
async def list_tags(request: Request, user: dict = Depends(current_user)) -> list[TagCount]:
    """Every distinct tag the caller uses, with how many conversations carry it,
    most-used first."""
    db = _db(request)
    cur = await db.execute(
        """
        SELECT t.tag, COUNT(*) AS n
        FROM conversation_tags t
        JOIN conversations c ON c.id = t.conversation_id
        WHERE c.user_id = ?
        GROUP BY t.tag
        ORDER BY n DESC, t.tag COLLATE NOCASE
        """,
        (user["id"],),
    )
    return [TagCount(tag=r[0], count=int(r[1])) for r in await cur.fetchall()]


@router.post("/rename")
async def rename_tag(
    body: RenameBody, request: Request, user: dict = Depends(current_user)
) -> dict:
    """Rename a tag across all the caller's conversations. If a conversation
    already has the target tag, the two merge (no duplicate — the table's PK is
    (conversation_id, tag))."""
    db = _db(request)
    old = body.old.strip()
    new = body.new.strip()[:40]
    if not new:
        raise HTTPException(status_code=400, detail="new tag is empty")
    if old.lower() == new.lower():
        return await _rename_result(db, user["id"], new)
    owned = "conversation_id IN (SELECT id FROM conversations WHERE user_id = ?)"
    async with db.transaction():
        # Add `new` to every conversation that has `old` (ignored where present),
        # then drop `old` — a clean merge under the composite PK.
        await db.execute(
            f"INSERT OR IGNORE INTO conversation_tags (conversation_id, tag) "
            f"SELECT conversation_id, ? FROM conversation_tags WHERE tag = ? AND {owned}",
            (new, old, user["id"]),
        )
        await db.execute(
            f"DELETE FROM conversation_tags WHERE tag = ? AND {owned}",
            (old, user["id"]),
        )
    return await _rename_result(db, user["id"], new)


async def _rename_result(db, uid: int, tag: str) -> dict:
    cur = await db.execute(
        "SELECT COUNT(*) FROM conversation_tags t JOIN conversations c "
        "ON c.id = t.conversation_id WHERE c.user_id = ? AND t.tag = ?",
        (uid, tag),
    )
    return {"tag": tag, "count": int((await cur.fetchone())[0])}


@router.delete("/{tag}", status_code=204)
async def delete_tag(tag: str, request: Request, user: dict = Depends(current_user)) -> None:
    """Remove a tag from every one of the caller's conversations."""
    db = _db(request)
    await db.execute(
        "DELETE FROM conversation_tags WHERE tag = ? AND "
        "conversation_id IN (SELECT id FROM conversations WHERE user_id = ?)",
        (tag, user["id"]),
    )
    await db.commit()
