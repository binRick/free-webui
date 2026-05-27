import json
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .config import settings
from .schemas import ChatRequest, ModelInfo, ModelList


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(
        base_url=settings.upstream_base_url,
        headers={"Authorization": f"Bearer {settings.upstream_api_key}"},
        timeout=httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0),
    )
    try:
        yield
    finally:
        await app.state.http.aclose()


app = FastAPI(title="free-webui", version="0.0.1", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/models", response_model=ModelList)
async def list_models() -> ModelList:
    client: httpx.AsyncClient = app.state.http
    try:
        r = await client.get("/models")
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"upstream models error: {e}")
    payload = r.json()
    data = [ModelInfo(id=m["id"]) for m in payload.get("data", [])]
    return ModelList(data=data)


async def _stream_chat(payload: dict) -> AsyncIterator[bytes]:
    """Proxy an OpenAI-compatible streaming chat completion as SSE.

    Re-emits upstream `data: {...}` lines verbatim, plus a final `data: [DONE]`.
    """
    client: httpx.AsyncClient = app.state.http
    async with client.stream("POST", "/chat/completions", json=payload) as r:
        if r.status_code >= 400:
            body = await r.aread()
            yield f"data: {json.dumps({'error': body.decode(errors='replace')})}\n\n".encode()
            return
        async for line in r.aiter_lines():
            if not line:
                continue
            # Upstream lines already look like `data: {...}` or `data: [DONE]`.
            yield f"{line}\n\n".encode()


@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    payload = {
        "model": req.model or settings.default_model,
        "messages": [m.model_dump() for m in req.messages],
        "stream": True,
    }
    if req.temperature is not None:
        payload["temperature"] = req.temperature

    return StreamingResponse(
        _stream_chat(payload),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
