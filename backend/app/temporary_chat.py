"""Temporary chat: a stateless streaming completion that is NEVER persisted.

The client holds the whole transcript in memory and replays it each turn; the
server streams a reply and writes nothing to the database (no conversation, no
messages). Tools/RAG are off (those need a persisted conversation), but a user
turn may carry inline ``data:`` images (vision input — used by the voice/video
call mode). Access is auth-gated and model-access-gated like the main chat.
"""
from typing import Literal

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
_MAX_IMAGES = 6
_MAX_IMAGE_CHARS = 8 * 1024 * 1024  # ~6 MB of binary as base64; per data: URL
_ROLES = {"user", "assistant", "system"}


class TextPart(BaseModel):
    type: Literal["text"]
    text: str


class ImageURL(BaseModel):
    url: str


class ImagePart(BaseModel):
    type: Literal["image_url"]
    image_url: ImageURL


class TemporaryMessage(BaseModel):
    role: str
    # Plain text, or multimodal parts (text + inline data: images for vision).
    content: str | list[TextPart | ImagePart]


class TemporaryBody(BaseModel):
    messages: list[TemporaryMessage] = Field(min_length=1, max_length=_MAX_MESSAGES)
    model: str | None = None
    system_prompt: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = Field(default=None, ge=1, le=131072)


def _validate_history(messages: list[TemporaryMessage]) -> list[dict]:
    """Normalise to upstream ``{role, content}`` dicts while bounding total text
    and inline-image size. Images must be ``data:`` URLs (no remote fetch), so a
    crafted transcript can't turn the upstream into an SSRF fetch agent."""
    text_total = 0
    image_count = 0
    out: list[dict] = []
    for m in messages:
        if m.role not in _ROLES:
            raise HTTPException(status_code=422, detail="invalid message role")
        if isinstance(m.content, str):
            text_total += len(m.content)
            out.append({"role": m.role, "content": m.content})
            continue
        parts: list[dict] = []
        for p in m.content:
            if isinstance(p, TextPart):
                text_total += len(p.text)
                parts.append({"type": "text", "text": p.text})
            else:
                url = p.image_url.url
                if not url.startswith("data:"):
                    raise HTTPException(status_code=422, detail="image url must be a data: URL")
                if len(url) > _MAX_IMAGE_CHARS:
                    raise HTTPException(status_code=413, detail="image too large")
                image_count += 1
                parts.append({"type": "image_url", "image_url": {"url": url}})
        out.append({"role": m.role, "content": parts})
    if image_count > _MAX_IMAGES:
        raise HTTPException(status_code=413, detail="too many images")
    if text_total > _MAX_TOTAL_CHARS:
        raise HTTPException(status_code=413, detail="transcript too large")
    return out


@router.post("/temporary")
async def temporary_chat(
    body: TemporaryBody, request: Request, user: dict = Depends(current_user)
) -> StreamingResponse:
    db = request.app.state.db
    http = request.app.state.http

    history = _validate_history(body.messages)

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
