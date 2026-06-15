"""Anthropic-compatible Messages API at /anthropic/v1/messages.

Lets an Anthropic SDK client (or Claude Code) target free-webui: point its
base_url at https://host/anthropic. We translate the Anthropic Messages request
into our OpenAI-compatible upstream call (routed by connection, access-checked),
then translate the response/stream back into Anthropic's shape.

Auth: the Anthropic SDK sends the key in `x-api-key`; we also accept a Bearer
token. Keys are the same per-user API keys minted at /account.
"""
import json
import secrets
import time
from typing import Any, AsyncIterator

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .access import can_access_model
from .api_keys import _hash_key
from .connections import conn_headers, conn_url, resolve_connection

router = APIRouter(prefix="/anthropic/v1", tags=["anthropic_compat"])


async def _user_from_key(request: Request, x_api_key: str | None, authorization: str | None) -> dict:
    token = (x_api_key or "").strip()
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="missing api key")
    db = request.app.state.db
    cur = await db.execute(
        """
        SELECT u.id, u.username, u.role, k.id
        FROM api_keys k JOIN users u ON u.id = k.user_id WHERE k.key_hash = ?
        """,
        (_hash_key(token),),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="invalid api key")
    await db.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (int(time.time()), row[3]))
    await db.commit()
    return {"id": row[0], "username": row[1], "role": row[2]}


# ---- translation: Anthropic request -> OpenAI request ----

def _system_text(system: Any) -> str:
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        return "\n".join(b.get("text", "") for b in system if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _content_to_openai(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[dict] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append({"type": "text", "text": block.get("text", "")})
        elif btype == "image":
            src = block.get("source", {})
            if isinstance(src, dict) and src.get("type") == "base64":
                url = f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}"
                parts.append({"type": "image_url", "image_url": {"url": url}})
        elif btype == "tool_result":
            # flatten tool results into text (we don't proxy tool use)
            inner = block.get("content")
            parts.append({"type": "text", "text": _content_to_openai(inner) if isinstance(inner, (str, list)) else ""})
    # collapse a single text part back to a string for maximum upstream compatibility
    if len(parts) == 1 and parts[0]["type"] == "text":
        return parts[0]["text"]
    return parts or ""


def _to_openai_body(body: dict) -> dict:
    msgs: list[dict] = []
    system = _system_text(body.get("system"))
    if system:
        msgs.append({"role": "system", "content": system})
    for m in body.get("messages", []):
        if isinstance(m, dict) and m.get("role") in ("user", "assistant"):
            msgs.append({"role": m["role"], "content": _content_to_openai(m.get("content"))})

    oai: dict[str, Any] = {"model": body["model"], "messages": msgs, "max_tokens": body["max_tokens"]}
    for src, dst in (("temperature", "temperature"), ("top_p", "top_p")):
        if body.get(src) is not None:
            oai[dst] = body[src]
    if body.get("stop_sequences"):
        oai["stop"] = body["stop_sequences"]
    return oai


# ---- translation: OpenAI response -> Anthropic response ----

def _msg_id() -> str:
    return "msg_" + secrets.token_hex(12)


_STOP_MAP = {"stop": "end_turn", "length": "max_tokens", "content_filter": "end_turn"}


def _to_anthropic_response(oai: dict, model: str) -> dict:
    choice = (oai.get("choices") or [{}])[0]
    text = (choice.get("message") or {}).get("content") or ""
    usage = oai.get("usage") or {}
    return {
        "id": _msg_id(),
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text if isinstance(text, str) else ""}],
        "stop_reason": _STOP_MAP.get(choice.get("finish_reason") or "stop", "end_turn"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


async def _anthropic_stream(http, conn, oai_body: dict, model: str) -> AsyncIterator[bytes]:
    mid = _msg_id()
    yield _sse("message_start", {
        "type": "message_start",
        "message": {
            "id": mid, "type": "message", "role": "assistant", "model": model,
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    })
    yield _sse("content_block_start", {
        "type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""},
    })
    yield _sse("ping", {"type": "ping"})
    stop_reason = "end_turn"
    try:
        async with http.stream(
            "POST", conn_url(conn, "chat/completions"), json={**oai_body, "stream": True},
            headers=conn_headers(conn),
        ) as r:
            if r.status_code >= 400:
                yield _sse("error", {"type": "error", "error": {"type": "api_error", "message": f"upstream {r.status_code}"}})
                return
            async for line in r.aiter_lines():
                if line.endswith("[DONE]"):
                    break
                if not line.startswith("data: "):
                    continue
                try:
                    chunk = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                choice = (chunk.get("choices") or [{}])[0]
                delta = (choice.get("delta") or {}).get("content")
                if isinstance(delta, str) and delta:
                    yield _sse("content_block_delta", {
                        "type": "content_block_delta", "index": 0,
                        "delta": {"type": "text_delta", "text": delta},
                    })
                if choice.get("finish_reason"):
                    stop_reason = _STOP_MAP.get(choice["finish_reason"], "end_turn")
    except httpx.HTTPError as e:
        yield _sse("error", {"type": "error", "error": {"type": "api_error", "message": str(e)}})
        return
    yield _sse("content_block_stop", {"type": "content_block_stop", "index": 0})
    yield _sse("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": {"output_tokens": 0},
    })
    yield _sse("message_stop", {"type": "message_stop"})


def _err(status: int, message: str, type_: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse(status_code=status, content={"type": "error", "error": {"type": type_, "message": message}})


@router.post("/messages")
async def messages(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
    authorization: str | None = Header(default=None),
):
    user = await _user_from_key(request, x_api_key, authorization)
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return _err(400, "invalid json body")
    if not isinstance(body, dict) or not isinstance(body.get("model"), str) or not body["model"]:
        return _err(400, "missing or invalid 'model'")
    if not isinstance(body.get("messages"), list) or not body["messages"]:
        return _err(400, "missing or invalid 'messages'")
    if not isinstance(body.get("max_tokens"), int):
        return _err(400, "missing or invalid 'max_tokens'")

    db = request.app.state.db
    if not await can_access_model(db, user, body["model"]):
        return _err(403, f"model '{body['model']}' is not available to this key", "permission_error")
    conn = await resolve_connection(request, db, body["model"])
    oai_body = _to_openai_body(body)
    http = request.app.state.http

    if bool(body.get("stream")):
        return StreamingResponse(
            _anthropic_stream(http, conn, oai_body, body["model"]), media_type="text/event-stream"
        )

    try:
        r = await http.post(
            conn_url(conn, "chat/completions"), json={**oai_body, "stream": False},
            headers=conn_headers(conn), timeout=300.0,
        )
    except httpx.HTTPError as e:
        return _err(502, f"upstream unreachable: {e}", "api_error")
    if r.status_code >= 400:
        return _err(502, r.text[:400], "api_error")
    try:
        oai = r.json()
    except json.JSONDecodeError:
        return _err(502, "upstream returned non-JSON", "api_error")
    return _to_anthropic_response(oai, body["model"])
