"""Server-side voice: STT/TTS proxies to an OpenAI-compatible audio backend."""
import httpx


def _mock_client(handler):
    return lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _signup(client):
    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})


# ---- status ----

async def test_audio_status_default_disabled(client):
    await _signup(client)
    r = await client.get("/api/audio/status")
    assert r.status_code == 200
    assert r.json()["stt"] is False
    assert r.json()["tts"] is False


async def test_audio_status_reports_configured(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "audio_stt_base_url", "https://stt.test/v1")
    monkeypatch.setattr(settings, "audio_tts_base_url", "https://tts.test/v1")
    monkeypatch.setattr(settings, "audio_tts_voice", "nova")
    await _signup(client)
    body = (await client.get("/api/audio/status")).json()
    assert body == {"stt": True, "tts": True, "voice": "nova"}


async def test_audio_status_requires_auth(client):
    # no session -> the router-level current_user dependency rejects
    assert (await client.get("/api/audio/status")).status_code == 401


# ---- transcriptions (STT) ----

async def test_transcribe_disabled_503(client):
    await _signup(client)
    r = await client.post(
        "/api/audio/transcriptions", files={"file": ("a.webm", b"x", "audio/webm")}
    )
    assert r.status_code == 503


async def test_transcribe_proxies_to_upstream(client, monkeypatch):
    from app import audio
    from app.config import settings

    monkeypatch.setattr(settings, "audio_stt_base_url", "https://stt.test/v1")
    monkeypatch.setattr(settings, "audio_stt_api_key", "sk-stt")
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["auth"] = req.headers.get("authorization")
        captured["body"] = req.content
        return httpx.Response(200, json={"text": "hello world"})

    monkeypatch.setattr(audio, "_client", _mock_client(handler))
    await _signup(client)
    r = await client.post(
        "/api/audio/transcriptions",
        files={"file": ("clip.webm", b"RIFFfakeaudio", "audio/webm")},
    )
    assert r.status_code == 200
    assert r.json() == {"text": "hello world"}
    assert captured["url"].endswith("/audio/transcriptions")
    assert captured["auth"] == "Bearer sk-stt"
    # the configured model + the uploaded bytes are forwarded as multipart
    assert b"whisper-1" in captured["body"]
    assert b"RIFFfakeaudio" in captured["body"]


async def test_transcribe_empty_upload_400(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "audio_stt_base_url", "https://stt.test/v1")
    await _signup(client)
    r = await client.post(
        "/api/audio/transcriptions", files={"file": ("a.webm", b"", "audio/webm")}
    )
    assert r.status_code == 400


async def test_transcribe_oversized_413(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "audio_stt_base_url", "https://stt.test/v1")
    monkeypatch.setattr(settings, "audio_max_upload_bytes", 16)
    await _signup(client)
    r = await client.post(
        "/api/audio/transcriptions", files={"file": ("a.webm", b"x" * 64, "audio/webm")}
    )
    assert r.status_code == 413


async def test_transcribe_upstream_error_502(client, monkeypatch):
    from app import audio
    from app.config import settings

    monkeypatch.setattr(settings, "audio_stt_base_url", "https://stt.test/v1")
    monkeypatch.setattr(
        audio, "_client", _mock_client(lambda req: httpx.Response(500, text="boom"))
    )
    await _signup(client)
    r = await client.post(
        "/api/audio/transcriptions", files={"file": ("a.webm", b"xx", "audio/webm")}
    )
    assert r.status_code == 502


# ---- speech (TTS) ----

async def test_speech_disabled_503(client):
    await _signup(client)
    assert (await client.post("/api/audio/speech", json={"input": "hi"})).status_code == 503


async def test_speech_proxies_and_returns_audio(client, monkeypatch):
    from app import audio
    from app.config import settings

    monkeypatch.setattr(settings, "audio_tts_base_url", "https://tts.test/v1")
    monkeypatch.setattr(settings, "audio_tts_api_key", "sk-tts")
    monkeypatch.setattr(settings, "audio_tts_voice", "alloy")
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        import json as _json

        captured["url"] = str(req.url)
        captured["auth"] = req.headers.get("authorization")
        captured["payload"] = _json.loads(req.content)
        return httpx.Response(
            200, content=b"ID3fake-mp3-bytes", headers={"content-type": "audio/mpeg"}
        )

    monkeypatch.setattr(audio, "_client", _mock_client(handler))
    await _signup(client)
    r = await client.post("/api/audio/speech", json={"input": "hello", "voice": "nova"})
    assert r.status_code == 200
    assert r.content == b"ID3fake-mp3-bytes"
    assert r.headers["content-type"].startswith("audio/")
    assert captured["url"].endswith("/audio/speech")
    assert captured["auth"] == "Bearer sk-tts"
    assert captured["payload"]["input"] == "hello"
    assert captured["payload"]["voice"] == "nova"  # per-request override wins
    assert captured["payload"]["model"] == "tts-1"


async def test_speech_empty_input_422(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "audio_tts_base_url", "https://tts.test/v1")
    await _signup(client)
    assert (await client.post("/api/audio/speech", json={"input": ""})).status_code == 422
