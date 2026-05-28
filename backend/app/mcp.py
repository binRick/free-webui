"""Minimal client for MCP (Model Context Protocol) servers over JSON-RPC
HTTP. Supports tools/list and tools/call. Per-user server configs;
discovered tools are merged with built-in tools and namespaced as
"mcp_{server_id}_{tool_name}" to avoid collisions."""
import json
import time
from typing import Any

import aiosqlite
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import current_user
from .tools import ToolContext, builtin_tool_specs
from .tools import run_tool_async as run_builtin_async

router = APIRouter(
    prefix="/api/mcp_servers",
    tags=["mcp"],
    dependencies=[Depends(current_user)],
)

MCP_PREFIX = "mcp_"
_MAX_RPC_TIMEOUT = 30.0


class ServerIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1)
    headers: dict[str, str] | None = None
    enabled: bool = True


class ServerPatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    url: str | None = None
    headers: dict[str, str] | None = None
    enabled: bool | None = None


class ServerOut(BaseModel):
    id: int
    name: str
    url: str
    headers: dict[str, str] | None
    enabled: bool
    created_at: int
    updated_at: int


def _db(request: Request) -> aiosqlite.Connection:
    return request.app.state.db


def _row_to_out(r) -> ServerOut:
    return ServerOut(
        id=r[0],
        name=r[1],
        url=r[2],
        headers=json.loads(r[3]) if r[3] else None,
        enabled=bool(r[4]),
        created_at=r[5],
        updated_at=r[6],
    )


@router.get("", response_model=list[ServerOut])
async def list_servers(request: Request, user: dict = Depends(current_user)):
    db = _db(request)
    cur = await db.execute(
        """
        SELECT id, name, url, headers, enabled, created_at, updated_at
        FROM mcp_servers WHERE user_id = ? ORDER BY id
        """,
        (user["id"],),
    )
    return [_row_to_out(r) for r in await cur.fetchall()]


@router.post("", response_model=ServerOut)
async def create_server(
    body: ServerIn, request: Request, user: dict = Depends(current_user)
):
    db = _db(request)
    now = int(time.time())
    headers_json = json.dumps(body.headers) if body.headers else None
    cur = await db.execute(
        """
        INSERT INTO mcp_servers (user_id, name, url, headers, enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user["id"], body.name, body.url, headers_json, int(body.enabled), now, now),
    )
    await db.commit()
    return ServerOut(
        id=cur.lastrowid,
        name=body.name,
        url=body.url,
        headers=body.headers,
        enabled=body.enabled,
        created_at=now,
        updated_at=now,
    )


@router.patch("/{sid}", response_model=ServerOut)
async def patch_server(
    sid: int,
    body: ServerPatch,
    request: Request,
    user: dict = Depends(current_user),
):
    db = _db(request)
    cur = await db.execute(
        """
        SELECT id, name, url, headers, enabled, created_at, updated_at
        FROM mcp_servers WHERE id = ? AND user_id = ?
        """,
        (sid, user["id"]),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="server not found")
    name = body.name if body.name is not None else row[1]
    url = body.url if body.url is not None else row[2]
    headers_json = (
        json.dumps(body.headers) if body.headers else (None if body.headers is not None else row[3])
    )
    enabled = (
        int(body.enabled) if body.enabled is not None else row[4]
    )
    now = int(time.time())
    await db.execute(
        """
        UPDATE mcp_servers
        SET name = ?, url = ?, headers = ?, enabled = ?, updated_at = ?
        WHERE id = ?
        """,
        (name, url, headers_json, enabled, now, sid),
    )
    await db.commit()
    return ServerOut(
        id=sid,
        name=name,
        url=url,
        headers=json.loads(headers_json) if headers_json else None,
        enabled=bool(enabled),
        created_at=row[5],
        updated_at=now,
    )


@router.delete("/{sid}", status_code=204)
async def delete_server(
    sid: int, request: Request, user: dict = Depends(current_user)
):
    db = _db(request)
    await db.execute(
        "DELETE FROM mcp_servers WHERE id = ? AND user_id = ?", (sid, user["id"])
    )
    await db.commit()


# ---- MCP JSON-RPC client ----

async def _rpc(url: str, headers: dict[str, str] | None, method: str, params: dict) -> dict:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    merged = {"content-type": "application/json", "accept": "application/json"}
    if headers:
        merged.update(headers)
    async with httpx.AsyncClient(timeout=_MAX_RPC_TIMEOUT, headers=merged) as c:
        r = await c.post(url, json=payload)
    if r.status_code >= 400:
        raise RuntimeError(f"mcp http {r.status_code}: {r.text[:200]}")
    try:
        body = r.json()
    except ValueError:
        raise RuntimeError("mcp returned non-JSON body")
    if "error" in body:
        err = body["error"]
        raise RuntimeError(f"mcp error: {err.get('message', err)}")
    return body.get("result") or {}


@router.post("/{sid}/probe")
async def probe_server(
    sid: int, request: Request, user: dict = Depends(current_user)
):
    """Hit tools/list against this server and return the discovered tools.
    Used by the settings UI to verify a server is reachable."""
    db = _db(request)
    cur = await db.execute(
        "SELECT url, headers FROM mcp_servers WHERE id = ? AND user_id = ?",
        (sid, user["id"]),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="server not found")
    url, headers_raw = row
    try:
        result = await _rpc(url, json.loads(headers_raw) if headers_raw else None, "tools/list", {})
    except (httpx.HTTPError, RuntimeError) as e:
        raise HTTPException(status_code=502, detail=str(e))
    return result


# ---- Composition + dispatch used by conversations.py ----

async def list_enabled_servers(
    db: aiosqlite.Connection, user_id: int
) -> list[dict]:
    cur = await db.execute(
        """
        SELECT id, name, url, headers FROM mcp_servers
        WHERE user_id = ? AND enabled = 1
        """,
        (user_id,),
    )
    rows = await cur.fetchall()
    return [
        {
            "id": r[0],
            "name": r[1],
            "url": r[2],
            "headers": json.loads(r[3]) if r[3] else None,
        }
        for r in rows
    ]


async def compose_tool_specs(
    db: aiosqlite.Connection, user_id: int
) -> tuple[list[dict], dict[str, tuple[int, str]]]:
    """Return (openai_tool_specs, dispatch_table).

    dispatch_table maps the namespaced tool name back to the (server_id,
    original_tool_name) so the executor can route the call. Built-in tools
    use server_id=0 and their unprefixed name.
    """
    builtins = builtin_tool_specs()
    specs: list[dict] = list(builtins)
    dispatch: dict[str, tuple[int, str]] = {}
    # Built-ins
    for spec in builtins:
        name = spec["function"]["name"]
        dispatch[name] = (0, name)

    servers = await list_enabled_servers(db, user_id)
    for s in servers:
        try:
            result = await _rpc(s["url"], s["headers"], "tools/list", {})
        except (httpx.HTTPError, RuntimeError):
            continue  # one bad server shouldn't kill the rest
        for tool in result.get("tools", []):
            orig_name = tool.get("name")
            if not orig_name:
                continue
            namespaced = f"{MCP_PREFIX}{s['id']}_{orig_name}"
            specs.append(
                {
                    "type": "function",
                    "function": {
                        "name": namespaced,
                        "description": tool.get("description") or f"[{s['name']}] {orig_name}",
                        "parameters": tool.get("inputSchema")
                        or {"type": "object", "properties": {}, "required": []},
                    },
                }
            )
            dispatch[namespaced] = (s["id"], orig_name)
    return specs, dispatch


async def run_tool(
    db: aiosqlite.Connection,
    user_id: int,
    dispatch: dict[str, tuple[int, str]],
    name: str,
    args: dict[str, Any],
    ctx: ToolContext | None = None,
) -> str:
    """Execute a tool by dispatched name. Built-ins (server_id=0) run
    locally; MCP tools fan out to the right server via tools/call."""
    target = dispatch.get(name)
    if target is None:
        return f"error: unknown tool {name!r}"
    server_id, real_name = target
    if server_id == 0:
        return await run_builtin_async(real_name, args, ctx)

    cur = await db.execute(
        "SELECT url, headers FROM mcp_servers WHERE id = ? AND user_id = ?",
        (server_id, user_id),
    )
    row = await cur.fetchone()
    if not row:
        return f"error: mcp server {server_id} no longer configured"
    url, headers_raw = row
    try:
        result = await _rpc(
            url,
            json.loads(headers_raw) if headers_raw else None,
            "tools/call",
            {"name": real_name, "arguments": args},
        )
    except (httpx.HTTPError, RuntimeError) as e:
        return f"error: {e}"
    content = result.get("content", [])
    parts = []
    for item in content:
        if item.get("type") == "text":
            parts.append(item.get("text", ""))
    return "\n".join(parts).strip() or json.dumps(result)
