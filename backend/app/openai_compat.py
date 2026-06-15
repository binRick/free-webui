"""OpenAI-compatible surface exposed at /v1/* under Bearer auth.

This lets external clients (OpenAI SDK, curl scripts, etc.) hit free-webui as if
it were OpenAI itself. The request body is forwarded to our upstream verbatim —
no RAG, no web search, no per-chat params — keeping the surface predictable for
SDK clients. Errors use the OpenAI error envelope so SDKs parse them correctly.
"""
import json
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .access import can_access_model, filter_models
from .api_keys import user_from_bearer

router = APIRouter(prefix="/v1", tags=["openai_compat"])


def _http(request: Request) -> httpx.AsyncClient:
    return request.app.state.http


def _error(status: int, message: str, type_: str = "invalid_request_error") -> JSONResponse:
    """OpenAI-shaped error body: {"error": {"message", "type"}}."""
    return JSONResponse(status_code=status, content={"error": {"message": message, "type": type_}})


def _error_frame(message: str, type_: str = "upstream_error") -> bytes:
    """An in-stream error the OpenAI SDK recognises, followed by [DONE]."""
    payload = json.dumps({"error": {"message": message, "type": type_}})
    return f"data: {payload}\n\ndata: [DONE]\n\n".encode()


def _passthrough_upstream(r: httpx.Response) -> JSONResponse:
    """Forward an upstream error: keep client 4xx (status + body) so SDKs see the
    real 400/404/429, and collapse upstream 5xx to a generic 502."""
    if 400 <= r.status_code < 500:
        try:
            body = r.json()
        except json.JSONDecodeError:
            body = {"error": {"message": r.text[:400], "type": "upstream_error"}}
        return JSONResponse(status_code=r.status_code, content=body)
    return _error(502, r.text[:400], "upstream_error")


@router.get("/models")
async def list_models(request: Request, user: dict = Depends(user_from_bearer)):
    http = _http(request)
    try:
        r = await http.get("/models")
    except httpx.HTTPError as e:
        return _error(502, f"upstream unreachable: {e}", "upstream_error")
    if r.status_code >= 400:
        return _passthrough_upstream(r)
    try:
        payload = r.json()
    except json.JSONDecodeError:
        return _error(502, "upstream returned non-JSON for models", "upstream_error")
    # Apply the same per-model access control as /api/models so a key can't even
    # see the names of models it isn't allowed to use.
    data = payload.get("data")
    if isinstance(data, list):
        ids = [m.get("id") for m in data if isinstance(m, dict) and m.get("id")]
        allowed = set(await filter_models(request.app.state.db, user, ids))
        payload["data"] = [m for m in data if isinstance(m, dict) and m.get("id") in allowed]
    return payload


def _validate_chat_body(body: object) -> str | None:
    if not isinstance(body, dict):
        return "request body must be a JSON object"
    model = body.get("model")
    if not isinstance(model, str) or not model:
        return "missing or invalid 'model'"
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return "missing or invalid 'messages'"
    return None


@router.post("/chat/completions")
async def chat_completions(request: Request, user: dict = Depends(user_from_bearer)):
    http = _http(request)
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return _error(400, "invalid json body")
    invalid = _validate_chat_body(body)
    if invalid:
        return _error(400, invalid)
    if not await can_access_model(request.app.state.db, user, body["model"]):
        return _error(403, f"model '{body['model']}' is not available to this key", "access_denied")

    if bool(body.get("stream")):
        async def proxy() -> AsyncIterator[bytes]:
            try:
                async with http.stream("POST", "/chat/completions", json=body) as r:
                    if r.status_code >= 400:
                        text = (await r.aread()).decode(errors="replace")
                        yield _error_frame(f"upstream error {r.status_code}: {text[:300]}")
                        return
                    async for chunk in r.aiter_bytes():
                        yield chunk
            except httpx.HTTPError as e:
                yield _error_frame(f"upstream unreachable: {e}")

        return StreamingResponse(proxy(), media_type="text/event-stream")

    try:
        r = await http.post("/chat/completions", json=body, timeout=300.0)
    except httpx.HTTPError as e:
        return _error(502, f"upstream unreachable: {e}", "upstream_error")
    if r.status_code >= 400:
        return _passthrough_upstream(r)
    try:
        return r.json()
    except json.JSONDecodeError:
        return _error(502, "upstream returned non-JSON for non-stream completion", "upstream_error")


@router.post("/embeddings")
async def embeddings(request: Request, _user: dict = Depends(user_from_bearer)):
    http = _http(request)
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return _error(400, "invalid json body")
    if not isinstance(body, dict):
        return _error(400, "request body must be a JSON object")
    # input may be a string or an array (incl. token-id arrays); let upstream
    # adjudicate emptiness/element validity rather than rejecting valid shapes.
    if not isinstance(body.get("input"), (str, list)):
        return _error(400, "missing or invalid 'input' (expected string or array)")
    if not isinstance(body.get("model"), str) or not body.get("model"):
        return _error(400, "missing or invalid 'model'")
    try:
        r = await http.post("/embeddings", json=body, timeout=120.0)
    except httpx.HTTPError as e:
        return _error(502, f"upstream unreachable: {e}", "upstream_error")
    if r.status_code >= 400:
        return _passthrough_upstream(r)
    try:
        return r.json()
    except json.JSONDecodeError:
        return _error(502, "upstream returned non-JSON for embeddings", "upstream_error")
