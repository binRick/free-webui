"""Test fixtures: per-test temp DB + ASGI client with a programmable upstream.

The upstream LLM is mocked via `httpx.MockTransport`. By default it echoes the
last user message (so the bulk of the suite needs no setup). For tests that must
drive hard paths in conversations.py (upstream errors, the tool loop, malformed
frames), request the `upstream` fixture and queue responses with `queue_chat`,
built from the `sse` / `content_chunk` / `tool_call_chunk` / `finish` /
`error_response` helpers.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections import deque
from pathlib import Path
from typing import AsyncIterator, Callable

import httpx
import pytest
import pytest_asyncio


# ---- default upstream behaviour (also reused by FakeUpstream) ----

def _fake_chat_stream(payload: dict) -> httpx.Response:
    messages = payload.get("messages", [])
    last_user = next(
        (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
    )
    if isinstance(last_user, list):
        last_user = " ".join(
            p.get("text", "") for p in last_user if p.get("type") == "text"
        )
    reply = f"echo: {last_user[:80]}".strip()
    chunks: list[bytes] = []
    for tok in reply.split(" "):
        chunks.append(
            f"data: {json.dumps({'choices':[{'delta':{'content': tok + ' '}}]})}\n\n".encode()
        )
    chunks.append(
        f"data: {json.dumps({'choices':[{'delta':{},'finish_reason':'stop'}]})}\n\n".encode()
    )
    chunks.append(b"data: [DONE]\n\n")
    return httpx.Response(
        200,
        content=b"".join(chunks),
        headers={"content-type": "text/event-stream"},
    )


def _fake_models() -> httpx.Response:
    return httpx.Response(200, json={"data": [{"id": "fake-a"}, {"id": "fake-b"}]})


def _fake_embeddings(body: dict) -> httpx.Response:
    inputs = body.get("input", [])
    if isinstance(inputs, str):
        inputs = [inputs]
    # Deterministic toy embeddings: never the zero vector, so cosine similarity
    # is well-defined for any input.
    return httpx.Response(
        200,
        json={
            "data": [
                {"embedding": [1.0 + (float(len(t) % 7) / 7.0)] * 8, "index": i}
                for i, t in enumerate(inputs)
            ]
        },
    )


async def _fake_handler(request: httpx.Request) -> httpx.Response:
    """Stateless default handler (kept for tests that import it directly)."""
    if request.url.path.endswith("/chat/completions"):
        return _fake_chat_stream(json.loads(request.content))
    if request.url.path.endswith("/models"):
        return _fake_models()
    if request.url.path.endswith("/embeddings"):
        return _fake_embeddings(json.loads(request.content))
    return httpx.Response(404, json={"error": f"unhandled: {request.url.path}"})


# ---- SSE builders for programmable tests ----

def sse(*chunks: dict, done: bool = True) -> httpx.Response:
    """Build a streaming chat-completions Response from OpenAI delta dicts."""
    body = b"".join(f"data: {json.dumps(c)}\n\n".encode() for c in chunks)
    if done:
        body += b"data: [DONE]\n\n"
    return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})


def content_chunk(text: str) -> dict:
    return {"choices": [{"delta": {"content": text}}]}


def finish(reason: str = "stop") -> dict:
    return {"choices": [{"delta": {}, "finish_reason": reason}]}


def tool_call_chunk(name: str, arguments: str, id: str = "call_1", index: int = 0) -> dict:
    return {
        "choices": [{
            "delta": {
                "tool_calls": [{
                    "index": index,
                    "id": id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }]
            }
        }]
    }


def error_response(status: int = 500, body: str = "upstream boom") -> httpx.Response:
    return httpx.Response(status, content=body.encode())


# ---- programmable upstream controller ----

class FakeUpstream:
    """A MockTransport handler whose chat behaviour can be scripted per test.

    Records every chat-completions request body in `chat_calls`. `queue_chat`
    sets one response per upcoming chat call (callable(body)->Response or a bare
    Response); once the queue drains it falls back to `default_chat`.
    """

    def __init__(self) -> None:
        self._chat_queue: deque = deque()
        self.default_chat: Callable[[dict], httpx.Response] = _fake_chat_stream
        self.chat_calls: list[dict] = []
        self.embeddings_calls: list[dict] = []

    def queue_chat(self, *responses) -> "FakeUpstream":
        self._chat_queue.extend(responses)
        return self

    def set_chat(self, handler: Callable[[dict], httpx.Response]) -> "FakeUpstream":
        self.default_chat = handler
        return self

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/chat/completions"):
            body = json.loads(request.content)
            self.chat_calls.append(body)
            item = self._chat_queue.popleft() if self._chat_queue else self.default_chat
            return item(body) if callable(item) else item
        if path.endswith("/models"):
            return _fake_models()
        if path.endswith("/embeddings"):
            body = json.loads(request.content)
            self.embeddings_calls.append(body)
            return _fake_embeddings(body)
        return httpx.Response(404, json={"error": f"unhandled: {path}"})


@pytest.fixture
def upstream() -> FakeUpstream:
    return FakeUpstream()


@pytest_asyncio.fixture
async def client(upstream: FakeUpstream) -> AsyncIterator[httpx.AsyncClient]:
    tmp = Path(tempfile.mkdtemp(prefix="free-webui-test-"))
    os.environ["FREE_WEBUI_DB_PATH"] = str(tmp / "test.db")
    os.environ["FREE_WEBUI_SECRET_KEY"] = "test-secret-for-unit-tests"
    os.environ["FREE_WEBUI_SECRET_KEY_PATH"] = str(tmp / "secret.key")

    # Re-import every app.* module each test so env-driven settings are
    # fresh and module-level `from .config import settings` rebinds.
    import importlib
    import sys

    for mod in [m for m in list(sys.modules) if m == "app" or m.startswith("app.")]:
        del sys.modules[mod]
    config = importlib.import_module("app.config")
    config.settings.secret_key = "test-secret-for-unit-tests"
    config.settings.secret_key_path = str(tmp / "secret.key")
    # Relax egress/throttle policy by default so feature tests are unaffected;
    # dedicated security tests opt these back on via monkeypatch.
    config.settings.ssrf_protection = False
    config.settings.login_rate_limit = 0

    # Run the whole suite against Postgres by setting FREE_WEBUI_TEST_DATABASE_URL;
    # otherwise an isolated temp SQLite file (the default). For Postgres we reset
    # the schema each test for isolation.
    pg_url = os.environ.get("FREE_WEBUI_TEST_DATABASE_URL")
    if pg_url:
        import asyncpg  # noqa: WPS433

        admin = await asyncpg.connect(pg_url)
        await admin.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        await admin.close()
        config.settings.db_path = pg_url
    else:
        config.settings.db_path = str(tmp / "test.db")

    from app.db import open_db  # noqa: WPS433
    from app.main import app  # noqa: WPS433

    db = await open_db(config.settings.db_path)
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(upstream),
        base_url=config.settings.upstream_base_url,
    )
    app.state.db = db
    app.state.http = http

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        try:
            yield c
        finally:
            await http.aclose()
            await db.close()
