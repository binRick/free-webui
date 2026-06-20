import logging
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .admin_models import router as admin_models_router
from .access import filter_models
from .admin_access import router as admin_access_router
from .admin_permissions import router as admin_permissions_router
from .admin_connections import router as admin_connections_router
from .admin_feedback import router as admin_feedback_router
from .admin_users import router as admin_users_router
from .connections import merged_model_ids
from .anthropic_compat import router as anthropic_compat_router
from .api_keys import router as api_keys_router
from .appearance import admin_router as appearance_admin_router
from .appearance import public_router as appearance_public_router
from .audit import router as audit_router
from .auth import configure_rate_limiter, current_user
from .auth import router as auth_router
from .code_exec import router as code_router
from .collections import router as collections_router
from .config import settings
from .admin_analytics import router as admin_analytics_router
from .audio import router as audio_router
from .account import router as account_router
from .banners import router as banners_router
from .channels import hub as channel_hub
from .channels import router as channels_router
from .evaluations import router as evaluations_router
from .temporary_chat import router as temporary_chat_router
from .conversations import router as conversations_router
from .db import open_db
from .files import configure_store
from .files import router as files_router
from .objectstore import make_object_store
from .folders import router as folders_router
from .notes import router as notes_router
from .openapi_tools import router as openapi_tools_router
from .documents import router as documents_router
from .images import router as images_router
from .mcp import router as mcp_router
from .memories import router as memories_router
from .oidc import router as oidc_router
from .openai_compat import router as openai_compat_router
from .permissions import router as permissions_router
from .plugins import load as load_plugins
from .plugins import router as plugins_router
from .presets import router as presets_router
from .prompts import router as prompts_router
from .schemas import ModelInfo, ModelList
from .shares import router as shares_router
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
    """Cap the request body size (a coarse DoS backstop). Reads the limit live so
    tests can adjust it.

    A declared ``Content-Length`` over the cap is rejected up front. Requests
    *without* a Content-Length (``Transfer-Encoding: chunked``) would otherwise
    slip the header check and buffer unbounded, so the actual streamed bytes are
    metered too: once the cap is crossed the body is truncated (the endpoint then
    fails to parse it) rather than buffered in full."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        limit = settings.max_request_body_bytes
        if scope["type"] != "http" or not limit:
            await self.app(scope, receive, send)
            return
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

        received = 0
        tripped = False

        async def guarded_receive():
            nonlocal received, tripped
            if tripped:
                return {"type": "http.request", "body": b"", "more_body": False}
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    tripped = True
                    # Truncate: hand the app a terminal empty chunk so the
                    # oversized body is never fully buffered (parsing then 4xxs).
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        await self.app(scope, guarded_receive, send)


class RequestIdMiddleware:
    """Attach an X-Request-ID to every response (honoring an inbound one) so a
    request can be correlated across logs/proxies."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        inbound = None
        for key, value in scope.get("headers", []):
            if key == b"x-request-id":
                inbound = value
                break
        rid = inbound or uuid.uuid4().hex[:16].encode()

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                if not any(h[0] == b"x-request-id" for h in headers):
                    headers.append((b"x-request-id", rid))
            await send(message)

        await self.app(scope, receive, send_wrapper)


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
    app.state.db = await open_db(settings.database_url or settings.db_path)
    app.state.plugins = load_plugins(settings.plugins_dir)
    # Optional S3 object store for media blobs (None -> DB storage). Configured
    # as a process singleton the file helpers read.
    app.state.object_store = make_object_store()
    configure_store(app.state.object_store)
    # Redis (optional): cross-replica channel pub/sub + a global login throttle.
    app.state.redis = None
    if settings.redis_url:
        import redis.asyncio as aioredis  # lazy: only when Redis is configured

        await channel_hub.use_redis(settings.redis_url)
        app.state.redis = aioredis.from_url(settings.redis_url)
        configure_rate_limiter(app.state.redis)
    try:
        yield
    finally:
        await channel_hub.aclose()
        await app.state.http.aclose()
        await app.state.db.close()
        if app.state.object_store is not None:
            await app.state.object_store.aclose()
        configure_store(None)
        configure_rate_limiter(None)
        if app.state.redis is not None:
            await app.state.redis.aclose()


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
app.add_middleware(RequestIdMiddleware)
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
app.include_router(shares_router)
app.include_router(files_router)
app.include_router(folders_router)
app.include_router(notes_router)
app.include_router(prompts_router)
app.include_router(presets_router)
app.include_router(admin_models_router)
app.include_router(admin_users_router)
app.include_router(admin_access_router)
app.include_router(admin_permissions_router)
app.include_router(permissions_router)
app.include_router(admin_connections_router)
app.include_router(audit_router)
app.include_router(admin_feedback_router)
app.include_router(admin_analytics_router)
app.include_router(banners_router)
app.include_router(appearance_public_router)
app.include_router(appearance_admin_router)
app.include_router(evaluations_router)
app.include_router(account_router)
app.include_router(web_search_router)
app.include_router(api_keys_router)
app.include_router(memories_router)
app.include_router(openai_compat_router)
app.include_router(anthropic_compat_router)
app.include_router(mcp_router)
app.include_router(openapi_tools_router)
app.include_router(images_router)
app.include_router(code_router)
app.include_router(audio_router)
app.include_router(channels_router)
app.include_router(temporary_chat_router)
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
