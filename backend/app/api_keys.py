"""API keys + Bearer-token user resolution for the OpenAI-compatible
surface."""
import hashlib
import secrets
import time

import aiosqlite
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import current_user

router = APIRouter(
    prefix="/api/api_keys",
    tags=["api_keys"],
    dependencies=[Depends(current_user)],
)


def _db(request: Request) -> aiosqlite.Connection:
    return request.app.state.db


KEY_PREFIX = "fw_"


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class CreateKeyBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class KeyListed(BaseModel):
    id: int
    name: str
    key_prefix: str
    last_used_at: int | None
    created_at: int


class KeyMinted(KeyListed):
    key: str  # full raw key, only returned on creation


@router.get("", response_model=list[KeyListed])
async def list_keys(request: Request, user: dict = Depends(current_user)):
    db = _db(request)
    cur = await db.execute(
        """
        SELECT id, name, key_prefix, last_used_at, created_at
        FROM api_keys WHERE user_id = ? ORDER BY created_at DESC
        """,
        (user["id"],),
    )
    return [
        KeyListed(
            id=r[0], name=r[1], key_prefix=r[2],
            last_used_at=r[3], created_at=r[4],
        )
        for r in await cur.fetchall()
    ]


@router.post("", response_model=KeyMinted)
async def mint_key(
    body: CreateKeyBody, request: Request, user: dict = Depends(current_user)
):
    db = _db(request)
    # 32-byte url-safe random; prefix so users recognise it.
    raw_secret = secrets.token_urlsafe(32)
    raw_key = f"{KEY_PREFIX}{raw_secret}"
    key_prefix = raw_key[: len(KEY_PREFIX) + 6] + "…"
    key_hash = _hash_key(raw_key)
    now = int(time.time())
    key_id = await db.insert(
        """
        INSERT INTO api_keys
        (user_id, name, key_prefix, key_hash, last_used_at, created_at)
        VALUES (?, ?, ?, ?, NULL, ?)
        """,
        (user["id"], body.name, key_prefix, key_hash, now),
    )
    await db.commit()
    return KeyMinted(
        id=key_id,
        name=body.name,
        key_prefix=key_prefix,
        last_used_at=None,
        created_at=now,
        key=raw_key,
    )


@router.delete("/{kid}", status_code=204)
async def revoke_key(
    kid: int, request: Request, user: dict = Depends(current_user)
):
    db = _db(request)
    await db.execute(
        "DELETE FROM api_keys WHERE id = ? AND user_id = ?", (kid, user["id"])
    )
    await db.commit()


# ----- Bearer auth dependency for /v1/* -----

async def user_from_bearer(
    request: Request, authorization: str | None = Header(default=None)
) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="empty token")
    db = _db(request)
    cur = await db.execute(
        """
        SELECT u.id, u.username, u.role, k.id, u.disabled
        FROM api_keys k JOIN users u ON u.id = k.user_id
        WHERE k.key_hash = ?
        """,
        (_hash_key(token),),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="invalid api key")
    if row[4]:
        # A suspended user's keys stop working too — disabling covers /v1, not
        # just the cookie session.
        raise HTTPException(status_code=403, detail="account disabled")
    await db.execute(
        "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
        (int(time.time()), row[3]),
    )
    await db.commit()
    return {"id": row[0], "username": row[1], "role": row[2]}
