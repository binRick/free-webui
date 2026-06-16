"""Model evaluation suite: a feedback-driven leaderboard plus a blind A/B
"arena" vote between two models.

Two independent signals per model are surfaced together:

* **Feedback** — the 👍/👎 captured on assistant messages (``message_feedback``
  joined to the model that produced the message), summarised as up/down counts
  and a Wilson lower-bound score that is robust to small sample sizes.
* **Arena** — pairwise blind votes (``arena_votes``). ELO ratings are computed
  on read by replaying every vote in ``(created_at, id)`` order, so there is no
  derived rating state to keep consistent with the vote log.

The leaderboard is visible to any authenticated user (like open-webui); the raw
vote log is admin-only.
"""
import math
import time
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .access import filter_models
from .auth import current_user, require_admin
from .connections import merged_model_ids

router = APIRouter(prefix="/api/evaluations", tags=["evaluations"])

_WINNERS = {"a", "b", "tie", "both_bad"}
_ELO_K = 32.0
_ELO_BASE = 1000.0
_PROMPT_SNIPPET = 280


def _wilson_lower_bound(up: int, total: int, z: float = 1.96) -> float:
    """Wilson score lower bound of the positive fraction at 95% confidence.

    Ranks a model with 9/10 👍 above one with 1/1 👍 — a small sample can't claim
    a high score. Returns a value in [0, 1]; 0 when there is no feedback.
    """
    if total <= 0:
        return 0.0
    phat = up / total
    denom = 1 + z * z / total
    centre = phat + z * z / (2 * total)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * total)) / total)
    return max(0.0, (centre - margin) / denom)


class LeaderboardRow(BaseModel):
    model: str
    # Feedback signal.
    up: int
    down: int
    feedback_count: int
    rating: float  # Wilson lower bound in [0, 1]
    # Arena signal.
    elo: int
    arena_games: int
    wins: int
    losses: int
    ties: int


class ArenaVoteIn(BaseModel):
    model_a: str = Field(min_length=1, max_length=200)
    model_b: str = Field(min_length=1, max_length=200)
    winner: str
    prompt: str | None = None


class ArenaVoteRow(BaseModel):
    id: int
    model_a: str
    model_b: str
    winner: str
    prompt: str | None
    username: str | None
    created_at: int


def _compute_elo(votes: list[tuple[str, str, str]]) -> dict[str, dict[str, Any]]:
    """Replay ``(model_a, model_b, winner)`` votes (already in chronological
    order) into per-model ELO + win/loss/tie tallies. ``both_bad`` counts as a
    game played but moves no rating (neither model earned anything)."""
    stats: dict[str, dict[str, Any]] = {}

    def _seen(m: str) -> dict[str, Any]:
        return stats.setdefault(
            m, {"elo": _ELO_BASE, "games": 0, "wins": 0, "losses": 0, "ties": 0}
        )

    for a, b, winner in votes:
        sa, sb = _seen(a), _seen(b)
        sa["games"] += 1
        sb["games"] += 1
        ea = 1.0 / (1.0 + 10 ** ((sb["elo"] - sa["elo"]) / 400.0))
        eb = 1.0 - ea
        if winner == "a":
            score_a, score_b = 1.0, 0.0
            sa["wins"] += 1
            sb["losses"] += 1
        elif winner == "b":
            score_a, score_b = 0.0, 1.0
            sb["wins"] += 1
            sa["losses"] += 1
        elif winner == "tie":
            score_a = score_b = 0.5
            sa["ties"] += 1
            sb["ties"] += 1
        else:  # both_bad: a game, but no rating movement
            sa["ties"] += 1
            sb["ties"] += 1
            continue
        sa["elo"] += _ELO_K * (score_a - ea)
        sb["elo"] += _ELO_K * (score_b - eb)
    return stats


@router.get("/leaderboard", response_model=list[LeaderboardRow], dependencies=[Depends(current_user)])
async def leaderboard(request: Request):
    db: aiosqlite.Connection = request.app.state.db

    # Feedback aggregated per generating model. Only assistant messages carry a
    # model; legacy rows (model IS NULL) are excluded from per-model stats.
    cur = await db.execute(
        """
        SELECT m.model,
               SUM(CASE WHEN f.rating > 0 THEN 1 ELSE 0 END) AS up,
               SUM(CASE WHEN f.rating < 0 THEN 1 ELSE 0 END) AS down
        FROM message_feedback f
        JOIN messages m ON m.id = f.message_id
        WHERE m.model IS NOT NULL AND m.model != ''
        GROUP BY m.model
        """
    )
    feedback: dict[str, dict[str, int]] = {}
    for model, up, down in await cur.fetchall():
        feedback[model] = {"up": int(up or 0), "down": int(down or 0)}

    # Arena votes, oldest first, so the ELO replay is deterministic.
    cur = await db.execute(
        "SELECT model_a, model_b, winner FROM arena_votes ORDER BY created_at, id"
    )
    arena = _compute_elo([(a, b, w) for a, b, w in await cur.fetchall()])

    rows: list[LeaderboardRow] = []
    for model in set(feedback) | set(arena):
        fb = feedback.get(model, {"up": 0, "down": 0})
        up, down = fb["up"], fb["down"]
        total = up + down
        ar = arena.get(model, {"elo": _ELO_BASE, "games": 0, "wins": 0, "losses": 0, "ties": 0})
        rows.append(
            LeaderboardRow(
                model=model,
                up=up,
                down=down,
                feedback_count=total,
                rating=round(_wilson_lower_bound(up, total), 4),
                elo=round(ar["elo"]),
                arena_games=ar["games"],
                wins=ar["wins"],
                losses=ar["losses"],
                ties=ar["ties"],
            )
        )
    # Rank by arena ELO when there are any games, falling back to feedback rating
    # then sample size; finally by name for a stable order.
    rows.sort(key=lambda r: (r.elo if r.arena_games else 0, r.rating, r.feedback_count, r.model), reverse=True)
    return rows


@router.post("/arena/vote", status_code=201, dependencies=[Depends(current_user)])
async def arena_vote(body: ArenaVoteIn, request: Request, user: dict = Depends(current_user)):
    if body.winner not in _WINNERS:
        raise HTTPException(status_code=422, detail="winner must be a, b, tie, or both_bad")
    if body.model_a == body.model_b:
        raise HTTPException(status_code=422, detail="model_a and model_b must differ")
    db: aiosqlite.Connection = request.app.state.db
    # A user may only vote on models that actually exist AND that they can use.
    # Validating against the resolved available set (not just access grants)
    # stops the leaderboard being stuffed with phantom-model entries or ratings
    # for models the voter never saw.
    available = set(await filter_models(db, user, await merged_model_ids(request, db)))
    for m in (body.model_a, body.model_b):
        if m not in available:
            raise HTTPException(status_code=403, detail=f"model '{m}' is not available to you")
    snippet = (body.prompt or "").strip().replace("\n", " ")[:_PROMPT_SNIPPET] or None
    now = int(time.time())
    vid = await db.insert(
        """
        INSERT INTO arena_votes (user_id, model_a, model_b, winner, prompt, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (user["id"], body.model_a, body.model_b, body.winner, snippet, now),
    )
    await db.commit()
    return {"id": vid}


@router.get("/arena/votes", response_model=list[ArenaVoteRow], dependencies=[Depends(require_admin)])
async def arena_votes(request: Request, limit: int = Query(100, ge=1, le=500)):
    db: aiosqlite.Connection = request.app.state.db
    cur = await db.execute(
        """
        SELECT v.id, v.model_a, v.model_b, v.winner, v.prompt, u.username, v.created_at
        FROM arena_votes v
        LEFT JOIN users u ON u.id = v.user_id
        ORDER BY v.id DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [
        ArenaVoteRow(
            id=r[0], model_a=r[1], model_b=r[2], winner=r[3],
            prompt=r[4], username=r[5], created_at=r[6],
        )
        for r in await cur.fetchall()
    ]
