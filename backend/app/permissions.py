"""Fine-grained per-feature permissions for non-admin users.

Each capability — web search, image generation, code interpreter, file upload,
external tool servers, knowledge bases, notes, temporary chat, public share
links — is a boolean permission. Every permission defaults to ALLOWED, so an
out-of-the-box install behaves exactly as before: an admin opts *into*
restriction, mirroring the "public unless restricted" model used for per-model
access (access.py).

Effective permission for a user:
  * admins are always allowed (they bypass the matrix entirely);
  * otherwise it is the global default for the key (``permission_defaults``,
    falling back to the built-in default of True) OR'd with any grant from a
    group the user belongs to (``group_permissions``).

Groups can only *widen* access above the default — to restrict a feature to a
group, set its default off and grant it to that group. This matches the mental
model people expect from Open WebUI's user-permission matrix without copying it.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from .access import user_group_ids
from .auth import current_user
from .database import Database

# key -> (human label, built-in default). The order here is the display order in
# the admin UI. Keys are the contract shared with the frontend + enforcement.
PERMISSIONS: dict[str, tuple[str, bool]] = {
    "web_search": ("Web search", True),
    "image_generation": ("Image generation", True),
    "code_interpreter": ("Code interpreter", True),
    "file_upload": ("File upload (RAG)", True),
    "tools": ("External tools (MCP / OpenAPI)", True),
    "knowledge": ("Knowledge bases", True),
    "notes": ("Notes", True),
    "temporary_chat": ("Temporary chat", True),
    "chat_share": ("Public share links", True),
}

PERMISSION_KEYS: tuple[str, ...] = tuple(PERMISSIONS.keys())


def _builtin_default(key: str) -> bool:
    meta = PERMISSIONS.get(key)
    return meta[1] if meta else True


async def get_defaults(db: Database) -> dict[str, bool]:
    """The global default for every known key (overrides merged over built-ins)."""
    cur = await db.execute("SELECT key, allowed FROM permission_defaults")
    overrides = {k: bool(a) for k, a in await cur.fetchall()}
    return {k: overrides.get(k, _builtin_default(k)) for k in PERMISSION_KEYS}


async def group_grants(db: Database) -> dict[int, list[str]]:
    """Granted keys per group id (only allowed=1 rows, only known keys)."""
    cur = await db.execute(
        "SELECT group_id, key FROM group_permissions WHERE allowed = 1"
    )
    out: dict[int, list[str]] = {}
    for gid, key in await cur.fetchall():
        if key in PERMISSIONS:
            out.setdefault(gid, []).append(key)
    return out


async def get_permissions(db: Database, user: dict) -> dict[str, bool]:
    """Effective permission map for ``user`` (admins get everything)."""
    if user.get("role") == "admin":
        return {k: True for k in PERMISSION_KEYS}
    eff = await get_defaults(db)
    gids = await user_group_ids(db, user["id"])
    if gids:
        placeholders = ",".join("?" * len(gids))
        cur = await db.execute(
            "SELECT DISTINCT key FROM group_permissions "
            f"WHERE allowed = 1 AND group_id IN ({placeholders})",  # noqa: S608
            tuple(gids),
        )
        for (key,) in await cur.fetchall():
            if key in eff:
                eff[key] = True
    return eff


async def has_permission(db: Database, user: dict, key: str) -> bool:
    if user.get("role") == "admin":
        return True
    return (await get_permissions(db, user)).get(key, _builtin_default(key))


def require_permission(key: str):
    """Endpoint dependency that 403s if the current user lacks ``key``.

    Reuses (and FastAPI-caches) ``current_user``, so adding it alongside an
    existing ``Depends(current_user)`` param costs only the permission lookup.

    Validates ``key`` at construction (import) time: an unknown key defaults to
    ALLOWED in ``has_permission``, so a typo'd gate would silently fail open —
    fail loudly here instead.
    """
    if key not in PERMISSIONS:
        raise ValueError(f"unknown permission key: {key!r}")

    async def _dep(request: Request, user: dict = Depends(current_user)) -> dict:
        if not await has_permission(request.app.state.db, user, key):
            raise HTTPException(status_code=403, detail=f"permission denied: {key}")
        return user

    return _dep


router = APIRouter(prefix="/api/permissions", tags=["permissions"])


@router.get("/me")
async def my_permissions(request: Request, user: dict = Depends(current_user)) -> dict:
    """The effective permissions for the current user, so the client can hide
    UI it isn't allowed to use. Enforcement is server-side regardless."""
    return await get_permissions(request.app.state.db, user)
