"""OpenAPI tool servers: register a URL to an OpenAPI (3.x, JSON) spec; its
operations become tools the model can call (alongside built-ins + MCP).

Per-user server configs. On compose, the spec is fetched + parsed into OpenAI
function specs (operationId -> tool name ``openapi_{server_id}_{op}``); on call,
the request is built from the operation (path/query/header/body params) and sent
to the resolved endpoint. Every outbound URL (spec fetch + each operation call)
is SSRF-guarded.
"""
import re
import time
from typing import Any
from urllib.parse import urlsplit

import aiosqlite
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import current_user
from .netguard import check_url

router = APIRouter(
    prefix="/api/openapi_servers", tags=["openapi"], dependencies=[Depends(current_user)]
)

OPENAPI_PREFIX = "openapi_"
_TIMEOUT = 30.0
_MAX_RESULT = 8000


# ---- models ----

class ServerIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1)  # the OpenAPI spec URL
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
    import json

    return ServerOut(
        id=r[0], name=r[1], url=r[2], headers=json.loads(r[3]) if r[3] else None,
        enabled=bool(r[4]), created_at=r[5], updated_at=r[6],
    )


_SELECT = "SELECT id, name, url, headers, enabled, created_at, updated_at FROM openapi_servers"


# ---- CRUD ----

@router.get("", response_model=list[ServerOut])
async def list_servers(request: Request, user: dict = Depends(current_user)):
    cur = await _db(request).execute(
        f"{_SELECT} WHERE user_id = ? ORDER BY created_at DESC", (user["id"],)
    )
    return [_row_to_out(r) for r in await cur.fetchall()]


@router.post("", response_model=ServerOut)
async def create_server(body: ServerIn, request: Request, user: dict = Depends(current_user)):
    import json

    db = _db(request)
    now = int(time.time())
    sid = await db.insert(
        "INSERT INTO openapi_servers (user_id, name, url, headers, enabled, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user["id"], body.name, body.url,
         json.dumps(body.headers) if body.headers else None, int(body.enabled), now, now),
    )
    await db.commit()
    return ServerOut(
        id=sid, name=body.name, url=body.url, headers=body.headers,
        enabled=body.enabled, created_at=now, updated_at=now,
    )


@router.patch("/{sid}", response_model=ServerOut)
async def patch_server(
    sid: int, body: ServerPatch, request: Request, user: dict = Depends(current_user)
):
    import json

    db = _db(request)
    cur = await db.execute(f"{_SELECT} WHERE id = ? AND user_id = ?", (sid, user["id"]))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="server not found")
    cur_out = _row_to_out(row)
    name = body.name if body.name is not None else cur_out.name
    url = body.url if body.url is not None else cur_out.url
    headers = body.headers if body.headers is not None else cur_out.headers
    enabled = body.enabled if body.enabled is not None else cur_out.enabled
    now = int(time.time())
    await db.execute(
        "UPDATE openapi_servers SET name = ?, url = ?, headers = ?, enabled = ?, updated_at = ? "
        "WHERE id = ?",
        (name, url, json.dumps(headers) if headers else None, int(enabled), now, sid),
    )
    await db.commit()
    return ServerOut(
        id=sid, name=name, url=url, headers=headers, enabled=enabled,
        created_at=cur_out.created_at, updated_at=now,
    )


@router.delete("/{sid}", status_code=204)
async def delete_server(sid: int, request: Request, user: dict = Depends(current_user)):
    db = _db(request)
    await db.execute("DELETE FROM openapi_servers WHERE id = ? AND user_id = ?", (sid, user["id"]))
    await db.commit()


# ---- spec parsing ----

def _resolve_ref(spec: dict, obj: Any, depth: int = 0) -> Any:
    """Resolve a local ``$ref`` (``#/a/b/c``) one level; bounded recursion."""
    if not isinstance(obj, dict) or depth > 10:
        return obj
    ref = obj.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        node: Any = spec
        for part in ref[2:].split("/"):
            if not isinstance(node, dict):
                return {}
            node = node.get(part, {})
        return _resolve_ref(spec, node, depth + 1)
    return obj


def _base_url(spec: dict, spec_url: str) -> str:
    """The operation base URL: the spec's first ``servers[].url`` (made absolute
    against the spec URL if relative), else the spec URL's origin."""
    origin = "{0.scheme}://{0.netloc}".format(urlsplit(spec_url))
    servers = spec.get("servers")
    if isinstance(servers, list) and servers and isinstance(servers[0], dict):
        u = servers[0].get("url", "")
        if u.startswith("http://") or u.startswith("https://"):
            return u
        if u:  # relative -> resolve against the spec origin
            return origin.rstrip("/") + "/" + u.lstrip("/")
    return origin


def spec_to_tools(spec: dict, spec_url: str, server_id: int, server_name: str):
    """(tool_specs, operations). operations[name] = {method, path, base_url, loc}."""
    base = _base_url(spec, spec_url)
    tools: list[dict] = []
    operations: dict[str, dict] = {}
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method in ("get", "post", "put", "patch", "delete"):
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            raw_id = op.get("operationId") or f"{method}_{path}"
            op_id = re.sub(r"[^A-Za-z0-9_]", "_", raw_id).strip("_")[:48] or method
            name = f"{OPENAPI_PREFIX}{server_id}_{op_id}"
            props: dict[str, Any] = {}
            required: list[str] = []
            loc: dict[str, str] = {}
            for p in op.get("parameters") or []:
                p = _resolve_ref(spec, p)
                pname, where = p.get("name"), p.get("in")
                if not pname or where not in ("query", "path", "header"):
                    continue
                schema = _resolve_ref(spec, p.get("schema") or {"type": "string"})
                props[pname] = {**schema, "description": p.get("description", "")}
                loc[pname] = where
                if p.get("required"):
                    required.append(pname)
            body = _resolve_ref(spec, op.get("requestBody") or {})
            jschema = _resolve_ref(
                spec, (body.get("content") or {}).get("application/json", {}).get("schema") or {}
            )
            if jschema.get("type") == "object":
                for bname, bs in (jschema.get("properties") or {}).items():
                    props[bname] = _resolve_ref(spec, bs)
                    loc[bname] = "body"
                required += list(jschema.get("required") or [])
            tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": (op.get("summary") or op.get("description")
                                    or f"[{server_name}] {op_id}")[:300],
                    "parameters": {
                        "type": "object", "properties": props, "required": sorted(set(required)),
                    },
                },
            })
            operations[name] = {"method": method, "path": path, "base_url": base, "loc": loc}
    return tools, operations


# ---- compose + dispatch (called from mcp.compose_tool_specs / run_tool) ----

async def list_enabled_servers(db: aiosqlite.Connection, user_id: int) -> list[dict]:
    import json

    cur = await db.execute(
        "SELECT id, name, url, headers FROM openapi_servers WHERE user_id = ? AND enabled = 1",
        (user_id,),
    )
    return [
        {"id": r[0], "name": r[1], "url": r[2], "headers": json.loads(r[3]) if r[3] else None}
        for r in await cur.fetchall()
    ]


async def _fetch_spec(url: str, headers: dict | None) -> dict:
    await check_url(url)
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = await c.get(url, headers=headers or {})
    if r.status_code >= 400:
        raise RuntimeError(f"openapi spec http {r.status_code}")
    try:
        return r.json()
    except ValueError:
        raise RuntimeError("openapi spec was not JSON")


async def compose_openapi_tools(db: aiosqlite.Connection, user_id: int):
    """Return (specs, dispatch) where dispatch[name] = ('openapi', server_id, op_meta)."""
    specs: list[dict] = []
    dispatch: dict[str, tuple] = {}
    for s in await list_enabled_servers(db, user_id):
        try:
            spec = await _fetch_spec(s["url"], s["headers"])
        except (httpx.HTTPError, RuntimeError):
            continue  # one bad server shouldn't kill the rest
        tools, operations = spec_to_tools(spec, s["url"], s["id"], s["name"])
        specs.extend(tools)
        for name, op in operations.items():
            dispatch[name] = ("openapi", s["id"], op)
    return specs, dispatch


async def run_openapi_tool(
    db: aiosqlite.Connection, user_id: int, server_id: int, op: dict, args: dict
) -> str:
    import json

    cur = await db.execute(
        "SELECT headers FROM openapi_servers WHERE id = ? AND user_id = ?", (server_id, user_id)
    )
    row = await cur.fetchone()
    if not row:
        return f"error: openapi server {server_id} no longer configured"
    server_headers = json.loads(row[0]) if row[0] else {}

    path, query, headers, body = op["path"], {}, dict(server_headers), {}
    for k, v in (args or {}).items():
        where = op["loc"].get(k)
        if where == "path":
            path = path.replace("{" + k + "}", str(v))
        elif where == "query":
            query[k] = v
        elif where == "header":
            headers[k] = str(v)
        elif where == "body":
            body[k] = v
    url = op["base_url"].rstrip("/") + path
    try:
        await check_url(url)  # SSRF guard the resolved endpoint
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            kwargs: dict[str, Any] = {"params": query, "headers": headers}
            if body and op["method"] in ("post", "put", "patch"):
                kwargs["json"] = body
            r = await c.request(op["method"].upper(), url, **kwargs)
    except (httpx.HTTPError, RuntimeError) as e:
        return f"error: {e}"
    return f"HTTP {r.status_code}\n{r.text[:_MAX_RESULT]}"
