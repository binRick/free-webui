"""Fixtures for the live integration suite.

Everything is function-scoped and the auth fixture is setup-or-login, so the
suite is re-runnable against a stack that's already up (e.g. `run.sh --keep`)
without tripping over pytest-asyncio's event-loop scoping.
"""
from __future__ import annotations

import asyncio
import json
import os

import httpx
import pytest
import pytest_asyncio

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
MODEL = os.environ.get("MODEL", "qwen2.5:1.5b")
ADMIN = {"username": "admin", "password": "integration-pass-123"}

# Generous timeout: real CPU inference can take a while for the first token.
_TIMEOUT = httpx.Timeout(180.0, connect=10.0)


async def _wait_for_health(client: httpx.AsyncClient, *, attempts: int = 60) -> None:
    for _ in range(attempts):
        try:
            r = await client.get("/api/health")
            if r.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(2)
    pytest.fail(f"backend at {BASE_URL} never became healthy")


@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=_TIMEOUT) as c:
        await _wait_for_health(c)
        yield c


@pytest_asyncio.fixture
async def admin(client):
    """An authenticated client. First run creates the admin via /setup; later
    runs (admin already exists) fall back to /login."""
    r = await client.post("/api/auth/setup", json=ADMIN)
    if r.status_code not in (200, 201):
        r = await client.post("/api/auth/login", json=ADMIN)
        assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return client


@pytest_asyncio.fixture
async def conversation(admin):
    r = await admin.post("/api/conversations", json={})
    assert r.status_code == 200, f"create conversation failed: {r.status_code} {r.text}"
    return r.json()["id"]


async def consume_chat(client: httpx.AsyncClient, cid: str, content, **patch):
    """Send a message and assemble the SSE stream.

    Returns (assistant_text, tool_events). `patch` kwargs (e.g. temperature=0,
    tools_enabled=True) are applied to the conversation first.
    """
    if patch:
        pr = await client.patch(f"/api/conversations/{cid}", json=patch)
        assert pr.status_code == 200, f"patch failed: {pr.status_code} {pr.text}"

    text = ""
    tool_events: list[dict] = []
    kind: str | None = None
    async with client.stream(
        "POST", f"/api/conversations/{cid}/messages", json={"content": content}
    ) as r:
        assert r.status_code == 200, (await r.aread()).decode(errors="replace")
        async for line in r.aiter_lines():
            line = line.rstrip("\r\n")
            if line == "":
                kind = None
                continue
            if line.startswith("event:"):
                kind = line[len("event:"):].strip()
                continue
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                kind = None
                continue
            if kind == "tool_call":
                tool_events.append(payload)
            else:
                delta = payload.get("choices", [{}])[0].get("delta", {}).get("content")
                if isinstance(delta, str):
                    text += delta
            kind = None
    return text, tool_events


@pytest.fixture
def chat():
    """Expose the SSE helper to tests as `await chat(client, cid, content, ...)`."""
    return consume_chat
