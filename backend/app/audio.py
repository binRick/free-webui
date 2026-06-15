"""Server-side voice: thin proxies to an OpenAI-compatible audio backend.

`/api/audio/transcriptions` forwards a recorded clip to a Whisper-style
`/audio/transcriptions` and returns the text; `/api/audio/speech` forwards text
to `/audio/speech` and streams the synthesized audio back. The upstream URLs and
keys are operator-configured (env) — never user-supplied — so there is no SSRF
surface; the upstream key is forwarded only to that upstream, never to the user.
Either direction is disabled (503) when its base URL is unset, and the client
falls back to the browser Web Speech API.
"""

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .auth import current_user
from .config import settings

router = APIRouter(
    prefix="/api/audio", tags=["audio"], dependencies=[Depends(current_user)]
)


class AudioStatus(BaseModel):
    stt: bool
    tts: bool
    voice: str | None = None


class SpeechIn(BaseModel):
    input: str = Field(min_length=1, max_length=8000)
    voice: str | None = Field(default=None, max_length=80)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=settings.audio_timeout_seconds)


@router.get("/status", response_model=AudioStatus)
async def audio_status() -> AudioStatus:
    return AudioStatus(
        stt=bool(settings.audio_stt_base_url),
        tts=bool(settings.audio_tts_base_url),
        voice=settings.audio_tts_voice or None,
    )


@router.post("/transcriptions")
async def transcribe(file: UploadFile = File(...)) -> dict:
    if not settings.audio_stt_base_url:
        raise HTTPException(status_code=503, detail="speech-to-text is not configured")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty audio upload")
    if len(data) > settings.audio_max_upload_bytes:
        raise HTTPException(status_code=413, detail="audio upload too large")

    base = settings.audio_stt_base_url.rstrip("/")
    headers = {}
    if settings.audio_stt_api_key:
        headers["authorization"] = f"Bearer {settings.audio_stt_api_key}"
    files = {
        "file": (
            file.filename or "audio.webm",
            data,
            file.content_type or "application/octet-stream",
        )
    }
    form = {"model": settings.audio_stt_model}
    async with _client() as c:
        try:
            r = await c.post(
                f"{base}/audio/transcriptions", files=files, data=form, headers=headers
            )
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"transcription upstream error: {e}")
    if r.status_code >= 400:
        raise HTTPException(
            status_code=502, detail=f"transcription upstream error {r.status_code}"
        )
    try:
        text = r.json().get("text", "")
    except ValueError:
        text = r.text
    return {"text": text}


@router.post("/speech")
async def speech(body: SpeechIn) -> Response:
    if not settings.audio_tts_base_url:
        raise HTTPException(status_code=503, detail="text-to-speech is not configured")
    base = settings.audio_tts_base_url.rstrip("/")
    headers = {"content-type": "application/json"}
    if settings.audio_tts_api_key:
        headers["authorization"] = f"Bearer {settings.audio_tts_api_key}"
    payload = {
        "model": settings.audio_tts_model,
        "input": body.input,
        "voice": body.voice or settings.audio_tts_voice,
        "response_format": settings.audio_tts_format,
    }
    async with _client() as c:
        try:
            r = await c.post(f"{base}/audio/speech", json=payload, headers=headers)
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"tts upstream error: {e}")
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"tts upstream error {r.status_code}")
    media = r.headers.get("content-type", "audio/mpeg")
    return Response(
        content=r.content, media_type=media, headers={"Cache-Control": "no-store"}
    )
