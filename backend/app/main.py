import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .admin_models import router as admin_models_router
from .access import filter_models
from .admin_access import router as admin_access_router
from .admin_connections import router as admin_connections_router
from .admin_users import router as admin_users_router
from .connections import merged_model_ids
from .anthropic_compat import router as anthropic_compat_router
from .api_keys import router as api_keys_router
from .auth import current_user
from .auth import router as auth_router
from .code_exec import router as code_router
from .collections import router as collections_router
from .config import settings
from .conversations import router as conversations_router
from .db import open_db
from .documents import router as documents_router
from .images import router as images_router
from .mcp import router as mcp_router
from .memories import router as memories_router
from .oidc import router as oidc_router
from .openai_compat import router as openai_compat_router
from .plugins import load as load_plugins
from .plugins import router as plugins_router
from .presets import router as presets_router
from .prompts import router as prompts_router
from .schemas import ModelInfo, ModelList
from .web_search import router as web_search_router

log = logging.getLogger("free_webui")


class SecurityHeadersMiddleware:
    """Pure-ASGI middleware that injects security headers on every response
    without buffering the body (so SSE streaming is unaffected)."""

    def __init__(self, app, headers: dict[str, str]):
        self.app = app
        self._headers = [(k.lower().encode(), v.encode()) for k, v in headers.items()]

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                present = {h[0] for h in headers}
                for key, value in self._headers:
                    if key not in present:
                        headers.append((key, value))
            await send(message)

        await self.app(scope, receive, send_wrapper)


class BodySizeLimitMiddleware:
    """Reject requests whose declared Content-Length exceeds the configured cap
    (a coarse DoS backstop). Reads the limit live so tests can adjust it."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        limit = settings.max_request_body_bytes
        if scope["type"] == "http" and limit:
            for key, value in scope.get("headers", []):
                if key == b"content-length":
                    try:
                        declared = int(value)
                    except ValueError:
                        declared = 0
                    if declared > limit:
                        body = b'{"detail":"request body too large"}'
                        await send({
                            "type": "http.response.start",
                            "status": 413,
                            "headers": [
                                (b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode()),
                            ],
                        })
                        await send({"type": "http.response.body", "body": body})
                        return
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # No default Authorization header: every request now carries its own
    # connection's auth (connections.conn_headers), so a keyless extra
    # connection can't inherit the default upstream's key.
    app.state.http = httpx.AsyncClient(
        base_url=settings.upstream_base_url,
        timeout=httpx.Timeout(
            connect=10.0,
            read=settings.upstream_read_timeout_seconds,
            write=30.0,
            pool=10.0,
        ),
    )
    app.state.db = await open_db(settings.db_path)
    app.state.plugins = load_plugins(settings.plugins_dir)
    try:
        yield
    finally:
        await app.state.http.aclose()
        await app.state.db.close()


app = FastAPI(title="free-webui", version="0.1.0", lifespan=lifespan)

_security_headers = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    # frame-ancestors backs up X-Frame-Options without breaking /docs scripts.
    "Content-Security-Policy": "frame-ancestors 'none'",
}
if settings.security_hsts:
    _security_headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"

# add_middleware prepends, so execution is outermost-first: CORS -> body-limit
# -> security-headers -> app. Security-headers is innermost so it wraps every
# app/exception-handler response; body-limit rejects oversized bodies pre-routing.
app.add_middleware(SecurityHeadersMiddleware, headers=_security_headers)
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "x-api-key", "anthropic-version"],
)


@app.exception_handler(Exception)
async def _unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    # Log the real error but return a generic envelope (no stack/internals leak).
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "internal server error"})

app.include_router(auth_router)
app.include_router(oidc_router)
app.include_router(conversations_router)
app.include_router(documents_router)
app.include_router(collections_router)
app.include_router(prompts_router)
app.include_router(presets_router)
app.include_router(admin_models_router)
app.include_router(admin_users_router)
app.include_router(admin_access_router)
app.include_router(admin_connections_router)
app.include_router(web_search_router)
app.include_router(api_keys_router)
app.include_router(memories_router)
app.include_router(openai_compat_router)
app.include_router(anthropic_compat_router)
app.include_router(mcp_router)
app.include_router(images_router)
app.include_router(code_router)
app.include_router(plugins_router)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/models", response_model=ModelList)
async def list_models(request: Request, user: dict = Depends(current_user)) -> ModelList:
    db = request.app.state.db
    ids = await merged_model_ids(request, db)
    allowed = await filter_models(db, user, ids)
    return ModelList(data=[ModelInfo(id=i) for i in allowed])
