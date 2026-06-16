"""Temporary chat: a stateless streaming completion that is NEVER persisted.

The client holds the whole transcript in memory and replays it each turn; the
server streams a reply and writes nothing to the database (no conversation, no
messages). Plain prose only — tools/RAG are off (those need a persisted
conversation). Access is auth-gated and model-access-gated like the main chat.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .auth import current_user
from .config import settings
from .connections import resolve_connection
from .conversations import (
    _build_upstream_messages,
    _guard_model,
    _stream_and_persist,
)

router = APIRouter(prefix="/api/chat", tags=["temporary"])

_MAX_MESSAGES = 200
_MAX_TOTAL_CHARS = 200_000
_ROLES = {"user", "assistant", "system"}


class TemporaryMessage(BaseModel):
    role: str
    content: str


class TemporaryBody(BaseModel):
    messages: list[TemporaryMessage] = Field(min_length=1, max_length=_MAX_MESSAGES)
    model: str | None = None
    system_prompt: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = Field(default=None, ge=1, le=131072)


@router.post("/temporary")
async def temporary_chat(
    body: TemporaryBody, request: Request, user: dict = Depends(current_user)
) -> StreamingResponse:
    db = request.app.state.db
    http = request.app.state.http

    history = [{"role": m.role, "content": m.content} for m in body.messages]
    if any(m["role"] not in _ROLES for m in history):
        raise HTTPException(status_code=422, detail="invalid message role")
    if sum(len(str(m["content"])) for m in history) > _MAX_TOTAL_CHARS:
        raise HTTPException(status_code=413, detail="transcript too large")

    await _guard_model(db, user, body.model, None)
    model = body.model or settings.default_model
    upstream = _build_upstream_messages(body.system_prompt, None, history)
    conn = await resolve_connection(request, db, model)
    gen = {"max_tokens": body.max_tokens}
    return StreamingResponse(
        _stream_and_persist(
            db, http, "", upstream, model,
            body.temperature, body.top_p, None,
            tools_enabled=False, user_id=user["id"], conn=conn, gen=gen,
            persist=False,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
