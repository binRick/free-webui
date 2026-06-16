"""Admin usage analytics: read-only aggregates over the existing tables.

All figures are instance-wide (totals across every user) and admin-only.
"""
import datetime
import time

import aiosqlite
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from .auth import require_admin

router = APIRouter(
    prefix="/api/admin/analytics", tags=["admin"], dependencies=[Depends(require_admin)]
)

_DAY = 86400


class DayCount(BaseModel):
    date: str
    count: int


class ModelCount(BaseModel):
    model: str
    count: int


class Analytics(BaseModel):
    totals: dict[str, int]  # users, conversations, messages, channels
    feedback: dict[str, int]  # up, down
    active_users_7d: int
    new_users_7d: int
    messages_per_day: list[DayCount]
    messages_per_model: list[ModelCount]


def _db(request: Request) -> aiosqlite.Connection:
    return request.app.state.db


async def _scalar(db: aiosqlite.Connection, sql: str, params: tuple = ()) -> int:
    row = await (await db.execute(sql, params)).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


@router.get("", response_model=Analytics)
async def analytics(
    request: Request, days: int = Query(default=30, ge=1, le=365)
) -> Analytics:
    db = _db(request)
    now = int(time.time())
    week = now - 7 * _DAY
    # The chart window starts at MIDNIGHT UTC of the earliest displayed day, so
    # the SQL filter and the zero-filled key list cover exactly the same days
    # (otherwise the oldest partial day is counted by SQL but dropped from the
    # chart). Message counts filter active=1 to match what users actually see —
    # superseded regenerate variants (active=0) are kept in the table but hidden.
    today = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).date()
    start_date = today - datetime.timedelta(days=days - 1)
    since = int(
        datetime.datetime(
            start_date.year, start_date.month, start_date.day,
            tzinfo=datetime.timezone.utc,
        ).timestamp()
    )

    totals = {
        "users": await _scalar(db, "SELECT COUNT(*) FROM users"),
        "conversations": await _scalar(db, "SELECT COUNT(*) FROM conversations"),
        "messages": await _scalar(db, "SELECT COUNT(*) FROM messages WHERE active = 1"),
        "channels": await _scalar(db, "SELECT COUNT(*) FROM channels"),
    }

    feedback = {
        "up": await _scalar(db, "SELECT COUNT(*) FROM message_feedback WHERE rating = 1"),
        "down": await _scalar(db, "SELECT COUNT(*) FROM message_feedback WHERE rating = -1"),
    }

    active_users_7d = await _scalar(
        db,
        "SELECT COUNT(DISTINCT c.user_id) FROM messages m "
        "JOIN conversations c ON c.id = m.conversation_id "
        "WHERE m.active = 1 AND m.created_at >= ?",
        (week,),
    )
    new_users_7d = await _scalar(
        db, "SELECT COUNT(*) FROM users WHERE created_at >= ?", (week,)
    )

    # Messages per (UTC) day, zero-filled across the requested window.
    cur = await db.execute(
        "SELECT strftime('%Y-%m-%d', created_at, 'unixepoch') AS d, COUNT(*) "
        "FROM messages WHERE active = 1 AND created_at >= ? GROUP BY d",
        (since,),
    )
    by_day = {r[0]: int(r[1]) for r in await cur.fetchall()}
    today = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).date()
    messages_per_day = [
        DayCount(
            date=(today - datetime.timedelta(days=i)).isoformat(),
            count=by_day.get((today - datetime.timedelta(days=i)).isoformat(), 0),
        )
        for i in range(days - 1, -1, -1)
    ]

    # Top models by assistant-message volume (a message's model = its
    # conversation's model; NULL -> "(default)").
    cur = await db.execute(
        "SELECT COALESCE(c.model, '(default)') AS model, COUNT(*) AS n "
        "FROM messages m JOIN conversations c ON c.id = m.conversation_id "
        "WHERE m.active = 1 AND m.role = 'assistant' GROUP BY model ORDER BY n DESC LIMIT 10"
    )
    messages_per_model = [
        ModelCount(model=r[0], count=int(r[1])) for r in await cur.fetchall()
    ]

    return Analytics(
        totals=totals,
        feedback=feedback,
        active_users_7d=active_users_7d,
        new_users_7d=new_users_7d,
        messages_per_day=messages_per_day,
        messages_per_model=messages_per_model,
    )
