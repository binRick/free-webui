"""Test fixtures: per-test temp DB + ASGI client with a stubbed upstream LLM."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import AsyncIterator

import httpx
import pytest_asyncio


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


async def _fake_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/chat/completions"):
        body = json.loads(request.content)
        return _fake_chat_stream(body)
    if request.url.path.endswith("/models"):
        return httpx.Response(
            200, json={"data": [{"id": "fake-a"}, {"id": "fake-b"}]}
        )
    if request.url.path.endswith("/embeddings"):
        body = json.loads(request.content)
        inputs = body.get("input", [])
        if isinstance(inputs, str):
            inputs = [inputs]
        # Deterministic toy embeddings: never the zero vector, so cosine
        # similarity is well-defined for any input.
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "embedding": [1.0 + (float(len(t) % 7) / 7.0)] * 8,
                        "index": i,
                    }
                    for i, t in enumerate(inputs)
                ]
            },
        )
    return httpx.Response(404, json={"error": f"unhandled: {request.url.path}"})


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
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
    config.settings.db_path = str(tmp / "test.db")
    config.settings.secret_key = "test-secret-for-unit-tests"
    config.settings.secret_key_path = str(tmp / "secret.key")

    from app.db import open_db  # noqa: WPS433
    from app.main import app  # noqa: WPS433

    db = await open_db(config.settings.db_path)
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(_fake_handler),
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
