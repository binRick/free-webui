"""Admin endpoints that proxy Ollama's native /api/* model-management routes."""
import json
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .auth import require_admin
from .config import settings

router = APIRouter(
    prefix="/api/admin/models",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


class InstalledModel(BaseModel):
    name: str
    size: int | None = None
    modified_at: str | None = None
    digest: str | None = None


class PullBody(BaseModel):
    name: str


def _native_url() -> str:
    if settings.ollama_admin_url:
        return settings.ollama_admin_url.rstrip("/")
    base = settings.upstream_base_url.rstrip("/")
    return base.removesuffix("/v1") if base.endswith("/v1") else base


@router.get("", response_model=list[InstalledModel])
async def list_installed():
    url = _native_url() + "/api/tags"
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(url)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"upstream unreachable: {e}")
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=r.text[:400])
    body = r.json()
    out: list[InstalledModel] = []
    for m in body.get("models", []):
        out.append(
            InstalledModel(
                name=m.get("name", ""),
                size=m.get("size"),
                modified_at=m.get("modified_at"),
                digest=m.get("digest"),
            )
        )
    return out


@router.post("/pull")
async def pull_model(body: PullBody) -> StreamingResponse:
    url = _native_url() + "/api/pull"
    payload = {"name": body.name, "stream": True}

    async def gen() -> AsyncIterator[bytes]:
        async with httpx.AsyncClient(timeout=None) as c:
            try:
                async with c.stream("POST", url, json=payload) as r:
                    if r.status_code >= 400:
                        err = (await r.aread()).decode(errors="replace")
                        yield (json.dumps({"error": err}) + "\n").encode()
                        return
                    async for chunk in r.aiter_lines():
                        if not chunk:
                            continue
                        yield (chunk + "\n").encode()
            except httpx.HTTPError as e:
                yield (json.dumps({"error": f"upstream error: {e}"}) + "\n").encode()

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@router.delete("", status_code=204)
async def delete_model(name: str = Query(min_length=1)):
    url = _native_url() + "/api/delete"
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.request("DELETE", url, json={"name": name})
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"upstream unreachable: {e}")
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=r.text[:400])
