"""Admin-broadcast banners shown to every signed-in user (maintenance notices,
announcements). Admins manage them; all authenticated users read the active set.
Dismissal is client-side (per-banner, in the browser)."""
import time

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import current_user, require_admin

_TYPES = {"info", "warning", "error", "success"}

router = APIRouter(prefix="/api", tags=["banners"])


class BannerIn(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    type: str = "info"
    dismissible: bool = True


class BannerOut(BaseModel):
    id: int
    content: str
    type: str
    dismissible: bool
    created_at: int


def _db(request: Request) -> aiosqlite.Connection:
    return request.app.state.db


def _row(r) -> BannerOut:
    return BannerOut(id=r[0], content=r[1], type=r[2], dismissible=bool(r[3]), created_at=r[4])


@router.get("/banners", response_model=list[BannerOut], dependencies=[Depends(current_user)])
async def list_active_banners(request: Request) -> list[BannerOut]:
    cur = await _db(request).execute(
        "SELECT id, content, type, dismissible, created_at FROM banners ORDER BY created_at DESC"
    )
    return [_row(r) for r in await cur.fetchall()]


@router.post("/admin/banners", response_model=BannerOut)
async def create_banner(
    body: BannerIn, request: Request, admin: dict = Depends(require_admin)
) -> BannerOut:
    if body.type not in _TYPES:
        raise HTTPException(status_code=422, detail=f"type must be one of {sorted(_TYPES)}")
    db = _db(request)
    now = int(time.time())
    bid = await db.insert(
        "INSERT INTO banners (content, type, dismissible, created_by, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (body.content, body.type, int(body.dismissible), admin["id"], now),
    )
    await db.commit()
    return BannerOut(
        id=bid, content=body.content, type=body.type,
        dismissible=body.dismissible, created_at=now,
    )


@router.delete("/admin/banners/{banner_id}", status_code=204)
async def delete_banner(
    banner_id: int, request: Request, admin: dict = Depends(require_admin)
) -> None:
    db = _db(request)
    await db.execute("DELETE FROM banners WHERE id = ?", (banner_id,))
    await db.commit()
