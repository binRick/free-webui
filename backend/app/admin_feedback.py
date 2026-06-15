"""Admin-only view of message feedback (the thumbs up/down captured per user)."""
import aiosqlite
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from .auth import require_admin

router = APIRouter(prefix="/api/admin/feedback", tags=["admin"], dependencies=[Depends(require_admin)])


class FeedbackRow(BaseModel):
    id: int
    rating: int
    comment: str | None
    username: str | None
    conversation_id: str | None
    conversation_title: str | None
    snippet: str
    created_at: int


def _snippet(content: str, n: int = 200) -> str:
    text = (content or "").replace("\n", " ").strip()
    return text[:n] + ("…" if len(text) > n else "")


@router.get("", response_model=list[FeedbackRow])
async def list_feedback(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    rating: int | None = Query(default=None, ge=-1, le=1),
):
    db: aiosqlite.Connection = request.app.state.db
    where = "WHERE f.rating = ?" if rating in (-1, 1) else ""
    params: list = [rating] if rating in (-1, 1) else []
    params.append(limit)
    cur = await db.execute(
        f"""
        SELECT f.id, f.rating, f.comment, f.updated_at, u.username,
               m.content, m.conversation_id, c.title
        FROM message_feedback f
        JOIN users u ON u.id = f.user_id
        JOIN messages m ON m.id = f.message_id
        LEFT JOIN conversations c ON c.id = m.conversation_id
        {where}
        ORDER BY f.id DESC
        LIMIT ?
        """,
        params,
    )
    return [
        FeedbackRow(
            id=r[0], rating=r[1], comment=r[2], created_at=r[3], username=r[4],
            snippet=_snippet(r[5]), conversation_id=r[6], conversation_title=r[7],
        )
        for r in await cur.fetchall()
    ]
