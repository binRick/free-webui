from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .admin_models import router as admin_models_router
from .api_keys import router as api_keys_router
from .auth import router as auth_router
from .config import settings
from .conversations import router as conversations_router
from .db import open_db
from .documents import router as documents_router
from .memories import router as memories_router
from .openai_compat import router as openai_compat_router
from .presets import router as presets_router
from .prompts import router as prompts_router
from .schemas import ModelInfo, ModelList
from .web_search import router as web_search_router


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

app.include_router(auth_router)
app.include_router(conversations_router)
app.include_router(documents_router)
app.include_router(prompts_router)
app.include_router(presets_router)
app.include_router(admin_models_router)
app.include_router(web_search_router)
app.include_router(api_keys_router)
app.include_router(memories_router)
app.include_router(openai_compat_router)


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
