"""OpenAI-compatible surface exposed at /v1/* under Bearer auth.

This lets external clients (OpenAI SDK, curl scripts, etc.) hit free-webui
as if it were OpenAI itself. The request body is forwarded to our upstream
verbatim — no RAG, no web search, no per-chat params — keeping the surface
predictable for SDK clients."""
import json
from typing import Any, AsyncIterator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from .api_keys import user_from_bearer

router = APIRouter(prefix="/v1", tags=["openai_compat"])


def _http(request: Request) -> httpx.AsyncClient:
    return request.app.state.http


@router.get("/models")
async def list_models(request: Request, _user: dict = Depends(user_from_bearer)):
    http = _http(request)
    try:
        r = await http.get("/models")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"upstream unreachable: {e}")
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=r.text[:400])
    return r.json()


@router.post("/chat/completions")
async def chat_completions(
    request: Request, _user: dict = Depends(user_from_bearer)
):
    http = _http(request)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid json body")

    stream = bool(body.get("stream"))
    if stream:
        async def proxy() -> AsyncIterator[bytes]:
            try:
                async with http.stream(
                    "POST", "/chat/completions", json=body
                ) as r:
                    async for chunk in r.aiter_bytes():
                        yield chunk
            except httpx.HTTPError as e:
                yield f'data: {{"error":"upstream: {e}"}}\n\n'.encode()
        from fastapi.responses import StreamingResponse
        return StreamingResponse(proxy(), media_type="text/event-stream")

    try:
        r = await http.post("/chat/completions", json=body, timeout=300.0)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"upstream unreachable: {e}")
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=r.text[:400])
    try:
        return r.json()
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=502,
            detail="upstream returned non-JSON for non-stream completion",
        )
