"""Admin API for additional upstream connections.

Connections are admin-configured and trusted (often internal/LAN upstreams), so
no SSRF guard is applied here. API keys are write-only: responses expose only
`has_api_key`, never the secret.
"""
import json
import time

import aiosqlite
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import require_admin
from .connections import Connection, conn_headers, conn_url, invalidate_model_map

router = APIRouter(
    prefix="/api/admin/connections",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


def _db(request: Request) -> aiosqlite.Connection:
    return request.app.state.db


class ConnectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str | None = None
    headers: dict[str, str] | None = None
    enabled: bool = True


class ConnectionPatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = None  # send a new key to rotate; omit to keep
    headers: dict[str, str] | None = None
    enabled: bool | None = None


class ConnectionOut(BaseModel):
    id: int
    name: str
    base_url: str
    has_api_key: bool
    headers: dict[str, str] | None
    enabled: bool
    created_at: int
    updated_at: int


def _row_to_out(r) -> ConnectionOut:
    return ConnectionOut(
        id=r[0], name=r[1], base_url=r[2], has_api_key=bool(r[3]),
        headers=json.loads(r[4]) if r[4] else None, enabled=bool(r[5]),
        created_at=r[6], updated_at=r[7],
    )


_SELECT = (
    "SELECT id, name, base_url, api_key, headers, enabled, created_at, updated_at "
    "FROM connections"
)


@router.get("", response_model=list[ConnectionOut])
async def list_connections(request: Request):
    db = _db(request)
    cur = await db.execute(f"{_SELECT} ORDER BY id")
    return [_row_to_out(r) for r in await cur.fetchall()]


@router.post("", response_model=ConnectionOut)
async def create_connection(body: ConnectionIn, request: Request):
    db = _db(request)
    now = int(time.time())
    headers_json = json.dumps(body.headers) if body.headers else None
    cur = await db.execute(
        """
        INSERT INTO connections (name, base_url, api_key, headers, enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (body.name, body.base_url, body.api_key, headers_json, int(body.enabled), now, now),
    )
    await db.commit()
    invalidate_model_map(request.app)
    cur = await db.execute(f"{_SELECT} WHERE id = ?", (cur.lastrowid,))
    return _row_to_out(await cur.fetchone())


@router.patch("/{cid}", response_model=ConnectionOut)
async def patch_connection(cid: int, body: ConnectionPatch, request: Request):
    db = _db(request)
    cur = await db.execute(f"{_SELECT} WHERE id = ?", (cid,))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="connection not found")
    name = body.name if body.name is not None else row[1]
    base_url = body.base_url if body.base_url is not None else row[2]
    api_key = body.api_key if body.api_key is not None else row[3]
    if body.headers is not None:
        headers_json = json.dumps(body.headers) if body.headers else None
    else:
        headers_json = row[4]
    enabled = int(body.enabled) if body.enabled is not None else row[5]
    now = int(time.time())
    await db.execute(
        """
        UPDATE connections
        SET name = ?, base_url = ?, api_key = ?, headers = ?, enabled = ?, updated_at = ?
        WHERE id = ?
        """,
        (name, base_url, api_key, headers_json, enabled, now, cid),
    )
    await db.commit()
    invalidate_model_map(request.app)
    cur = await db.execute(f"{_SELECT} WHERE id = ?", (cid,))
    return _row_to_out(await cur.fetchone())


@router.delete("/{cid}", status_code=204)
async def delete_connection(cid: int, request: Request):
    db = _db(request)
    await db.execute("DELETE FROM connections WHERE id = ?", (cid,))
    await db.commit()
    invalidate_model_map(request.app)


@router.post("/test")
async def test_connection(body: ConnectionIn, request: Request) -> dict:
    """Probe a connection's /models without saving it."""
    conn = Connection(
        id=-1, name=body.name, base_url=body.base_url,
        api_key=body.api_key or "", headers=body.headers, enabled=True,
    )
    http: httpx.AsyncClient = request.app.state.http
    try:
        r = await http.get(conn_url(conn, "models"), headers=conn_headers(conn))
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"unreachable: {e}", "models": []}
    if r.status_code >= 400:
        return {"ok": False, "error": f"upstream http {r.status_code}", "models": []}
    try:
        payload = r.json()
        data = payload.get("data", []) if isinstance(payload, dict) else []
        if not isinstance(data, list):
            raise TypeError("'data' is not a list")
        models = [m["id"] for m in data if isinstance(m, dict) and m.get("id")]
    except (ValueError, AttributeError, TypeError):
        return {"ok": False, "error": "unexpected /models response shape", "models": []}
    return {"ok": True, "error": None, "models": models}
