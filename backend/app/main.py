from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .conversations import router as conversations_router
from .db import open_db
from .schemas import ModelInfo, ModelList


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(
        base_url=settings.upstream_base_url,
        headers={"Authorization": f"Bearer {settings.upstream_api_key}"},
        timeout=httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0),
    )
    app.state.db = await open_db(settings.db_path)
    try:
        yield
    finally:
        await app.state.http.aclose()
        await app.state.db.close()


app = FastAPI(title="free-webui", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(conversations_router)


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
