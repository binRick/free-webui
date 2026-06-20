"""Instance appearance: operator-tunable branding + custom CSS.

The settings live in the ``app_settings`` key→value table and are surfaced to
every client (including the unauthenticated login / setup / share views) via the
public ``GET /api/config`` endpoint, so a self-hoster can white-label the
instance name and inject site-wide CSS. Writes are admin-only.

Custom CSS is applied by the client into a ``<style>`` element via ``textContent``
(it is CSS, not HTML, and cannot execute script); it is authored by the operator
— the highest-trust role — so this is white-labeling, not a privilege boundary.
"""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator

from .audit import record
from .auth import require_admin
from .config import settings
from .database import Database

# key -> default. instance_name falls back to the configured default; custom_css
# is empty until an admin sets it.
_INSTANCE_NAME = "instance_name"
_CUSTOM_CSS = "custom_css"
_CSS_MAX = 100_000  # 100 KB cap on injected CSS


def _db(request: Request) -> Database:
    return request.app.state.db


async def _get(db: Database, key: str) -> str | None:
    cur = await db.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
    row = await cur.fetchone()
    return row[0] if row else None


async def _set(db: Database, key: str, value: str) -> None:
    # Upsert without dialect-specific ON CONFLICT (SQLite + Postgres).
    await db.execute("DELETE FROM app_settings WHERE key = ?", (key,))
    await db.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", (key, value))


async def get_appearance(db: Database) -> dict:
    name = await _get(db, _INSTANCE_NAME)
    css = await _get(db, _CUSTOM_CSS)
    return {
        "instance_name": name if name is not None else settings.instance_name,
        "custom_css": css or "",
    }


# ---- public config (no auth: login / setup / share need branding too) ----

public_router = APIRouter(prefix="/api", tags=["config"])


@public_router.get("/config")
async def get_config(request: Request) -> dict:
    """Public branding/appearance for the client. No secrets — only the instance
    name + custom CSS, readable before login."""
    return await get_appearance(_db(request))


# ---- admin write ----

admin_router = APIRouter(
    prefix="/api/admin/appearance", tags=["admin"], dependencies=[Depends(require_admin)]
)


class AppearanceIn(BaseModel):
    instance_name: str = Field(min_length=1, max_length=80)
    custom_css: str = Field(default="", max_length=_CSS_MAX)

    @field_validator("instance_name")
    @classmethod
    def _name_nonblank(cls, v: str) -> str:
        # strip-then-recheck so a whitespace-only name can't pass min_length and
        # then be stored (and served) as empty.
        v = v.strip()
        if not v:
            raise ValueError("instance_name cannot be blank")
        return v


@admin_router.get("")
async def read_appearance(request: Request) -> dict:
    return await get_appearance(_db(request))


@admin_router.put("")
async def write_appearance(
    body: AppearanceIn, request: Request, me: dict = Depends(require_admin)
) -> dict:
    db = _db(request)
    # instance_name is already stripped + validated non-blank by the model.
    async with db.transaction():
        await _set(db, _INSTANCE_NAME, body.instance_name)
        await _set(db, _CUSTOM_CSS, body.custom_css)
    await record(db, me, "appearance.update", f"name={body.instance_name!r} css={len(body.custom_css)}b")
    return await get_appearance(db)
