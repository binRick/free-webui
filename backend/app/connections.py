"""Multiple upstream connections.

The env-configured upstream is always present as connection id 0 ("default").
Admins can register additional OpenAI-compatible connections (base_url, api_key,
optional headers). All requests are carried over the single shared httpx client
(app.state.http) using ABSOLUTE urls + per-connection headers, so one transport
(and one MockTransport in tests) serves every connection.

A chat request is routed to the connection whose /models lists the requested
model (the default/env connection wins ties). The common single-upstream case
pays nothing: resolve_connection short-circuits when no extra connections exist.

CONTRACT: model ids form a GLOBAL namespace across connections. A model_access
grant applies to that id wherever it is served; when two connections advertise
the same id the env/default connection serves it; a model served by no enabled
connection falls back to the env connection (which then returns its own error).
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass

import aiosqlite
import httpx
from fastapi import Request

from .config import settings

# /models probes use a tight timeout so one slow/unreachable extra connection
# can't add the full client connect timeout to every chat turn.
_PROBE_TIMEOUT = httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=5.0)
# The model->connection map is cached briefly (keyed by the enabled-connection
# set) and invalidated on any connection change, so routing doesn't re-probe
# every upstream on every request.
_MODEL_MAP_TTL = 20.0


@dataclass
class Connection:
    id: int
    name: str
    base_url: str
    api_key: str
    headers: dict | None
    enabled: bool


def config_connection() -> Connection:
    return Connection(
        id=0,
        name="default",
        base_url=settings.upstream_base_url,
        api_key=settings.upstream_api_key,
        headers=None,
        enabled=True,
    )


def conn_headers(conn: Connection) -> dict[str, str]:
    h: dict[str, str] = {}
    if conn.api_key:
        h["Authorization"] = f"Bearer {conn.api_key}"
    if conn.headers:
        h.update(conn.headers)
    return h


def conn_url(conn: Connection, path: str) -> str:
    return conn.base_url.rstrip("/") + "/" + path.lstrip("/")


def _row_to_conn(r) -> Connection:
    return Connection(
        id=r[0], name=r[1], base_url=r[2], api_key=r[3] or "",
        headers=json.loads(r[4]) if r[4] else None, enabled=bool(r[5]),
    )


async def db_connections(db: aiosqlite.Connection, enabled_only: bool = False) -> list[Connection]:
    sql = "SELECT id, name, base_url, api_key, headers, enabled FROM connections"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY id"
    cur = await db.execute(sql)
    return [_row_to_conn(r) for r in await cur.fetchall()]


async def enabled_connections(db: aiosqlite.Connection) -> list[Connection]:
    """The env connection first, then every enabled DB connection."""
    return [config_connection(), *await db_connections(db, enabled_only=True)]


async def fetch_models(http: httpx.AsyncClient, conn: Connection) -> list[str]:
    """Model ids advertised by a connection. Any failure or malformed payload
    yields [] for that connection (never raises) so one bad upstream can't break
    listing/routing."""
    try:
        r = await http.get(conn_url(conn, "models"), headers=conn_headers(conn), timeout=_PROBE_TIMEOUT)
        r.raise_for_status()
        payload = r.json()
        data = payload.get("data", []) if isinstance(payload, dict) else []
        if not isinstance(data, list):
            return []
    except (httpx.HTTPError, json.JSONDecodeError, ValueError, TypeError, AttributeError):
        return []
    return [m["id"] for m in data if isinstance(m, dict) and m.get("id")]


def invalidate_model_map(app) -> None:
    """Drop the cached model->connection map (call after any connection change)."""
    app.state._model_map = None


async def model_connection_map(request: Request, db: aiosqlite.Connection) -> dict[str, Connection]:
    """model id -> serving connection (env connection wins ties).

    Cached for _MODEL_MAP_TTL seconds, keyed by the set of enabled connection
    ids, and invalidated on connection changes. Per-connection /models probes
    run concurrently so latency is the slowest single probe, not their sum.
    """
    cur = await db.execute("SELECT id FROM connections WHERE enabled = 1 ORDER BY id")
    key = tuple(r[0] for r in await cur.fetchall())
    cache = getattr(request.app.state, "_model_map", None)
    now = time.time()
    if cache and cache["key"] == key and cache["expires"] > now:
        return cache["map"]

    http: httpx.AsyncClient = request.app.state.http
    conns = await enabled_connections(db)
    results = await asyncio.gather(*(fetch_models(http, c) for c in conns))
    mapping: dict[str, Connection] = {}
    for conn, mids in zip(conns, results):  # env first -> wins ties via setdefault
        for mid in mids:
            mapping.setdefault(mid, conn)
    request.app.state._model_map = {"key": key, "map": mapping, "expires": now + _MODEL_MAP_TTL}
    return mapping


async def _has_extra_connections(db: aiosqlite.Connection) -> bool:
    cur = await db.execute("SELECT 1 FROM connections WHERE enabled = 1 LIMIT 1")
    return await cur.fetchone() is not None


async def resolve_connection(request: Request, db: aiosqlite.Connection, model: str | None) -> Connection:
    """The connection that should serve `model`. Fast-paths to the env upstream
    when no extra connections are configured."""
    if not await _has_extra_connections(db):
        return config_connection()
    if not model:
        return config_connection()
    mapping = await model_connection_map(request, db)
    return mapping.get(model) or config_connection()


async def merged_model_ids(request: Request, db: aiosqlite.Connection) -> list[str]:
    """Unique model ids across all enabled connections (env first), order-preserving."""
    if not await _has_extra_connections(db):
        http: httpx.AsyncClient = request.app.state.http
        return await fetch_models(http, config_connection())
    seen: list[str] = []
    present: set[str] = set()
    for mid in (await model_connection_map(request, db)).keys():
        if mid not in present:
            present.add(mid)
            seen.append(mid)
    return seen
