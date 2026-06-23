"""Admin usage analytics: read-only aggregates over the existing tables.

All figures are instance-wide (totals across every user) and admin-only.
"""
import datetime
import time

import aiosqlite
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from .auth import require_admin
from .config import settings

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


class ModelTokens(BaseModel):
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost: float | None = None  # USD, only when the model has a configured price


class Analytics(BaseModel):
    totals: dict[str, int]  # users, conversations, messages, channels
    feedback: dict[str, int]  # up, down
    active_users_7d: int
    new_users_7d: int
    messages_per_day: list[DayCount]
    messages_per_model: list[ModelCount]
    tokens: dict[str, int]  # prompt, completion, total (instance-wide)
    tokens_per_model: list[ModelTokens]
    cost_total: float | None  # USD across priced models; None if no prices set


def _model_cost(model: str, prompt: int, completion: int) -> float | None:
    """Cost in USD for prompt/completion tokens at the configured per-1M price,
    or None if this model has no price entry."""
    price = settings.model_prices.get(model)
    if not price:
        return None
    return round(
        prompt / 1_000_000 * float(price.get("input", 0))
        + completion / 1_000_000 * float(price.get("output", 0)),
        4,
    )


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

    # Top models by assistant-message volume. Prefer the model recorded on the
    # message itself (accurate even when a conversation switched models); fall
    # back to the conversation's model for legacy rows, then "(default)".
    cur = await db.execute(
        "SELECT COALESCE(m.model, c.model, '(default)') AS model, COUNT(*) AS n "
        "FROM messages m JOIN conversations c ON c.id = m.conversation_id "
        "WHERE m.active = 1 AND m.role = 'assistant' "
        "GROUP BY COALESCE(m.model, c.model, '(default)') ORDER BY n DESC LIMIT 10"
    )
    messages_per_model = [
        ModelCount(model=r[0], count=int(r[1])) for r in await cur.fetchall()
    ]

    # Token usage (instance-wide) — overall + per model. NULLs (replies whose
    # upstream didn't report usage) are ignored by SUM.
    row = await (
        await db.execute(
            "SELECT COALESCE(SUM(prompt_tokens), 0), COALESCE(SUM(completion_tokens), 0) "
            "FROM messages WHERE role = 'assistant'"
        )
    ).fetchone()
    tok_prompt, tok_completion = int(row[0]), int(row[1])
    tokens = {
        "prompt": tok_prompt,
        "completion": tok_completion,
        "total": tok_prompt + tok_completion,
    }

    cur = await db.execute(
        "SELECT COALESCE(m.model, c.model, '(default)') AS model, "
        "       COALESCE(SUM(m.prompt_tokens), 0), COALESCE(SUM(m.completion_tokens), 0) "
        "FROM messages m JOIN conversations c ON c.id = m.conversation_id "
        "WHERE m.role = 'assistant' "
        "GROUP BY COALESCE(m.model, c.model, '(default)') "
        "HAVING SUM(m.prompt_tokens) > 0 OR SUM(m.completion_tokens) > 0 "
        "ORDER BY (COALESCE(SUM(m.prompt_tokens), 0) + COALESCE(SUM(m.completion_tokens), 0)) DESC "
        "LIMIT 10"
    )
    tokens_per_model: list[ModelTokens] = []
    cost_total: float | None = None
    for model, p, comp in await cur.fetchall():
        p, comp = int(p), int(comp)
        cost = _model_cost(model, p, comp)
        if cost is not None:
            cost_total = round((cost_total or 0.0) + cost, 4)
        tokens_per_model.append(
            ModelTokens(
                model=model, prompt_tokens=p, completion_tokens=comp,
                total_tokens=p + comp, cost=cost,
            )
        )

    return Analytics(
        totals=totals,
        feedback=feedback,
        active_users_7d=active_users_7d,
        new_users_7d=new_users_7d,
        messages_per_day=messages_per_day,
        messages_per_model=messages_per_model,
        tokens=tokens,
        tokens_per_model=tokens_per_model,
        cost_total=cost_total,
    )
