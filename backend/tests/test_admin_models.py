"""Admin model endpoints. We stub the Ollama native API at the httpx level
via a monkeypatched httpx.AsyncClient — same pattern as the conversation
tests but for the OUTBOUND admin client (which is created per-request, so
we patch httpx.AsyncClient itself)."""
import json


async def _signup(client):
    await client.post(
        "/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"}
    )


async def _signup_user(client, username, password):
    """Create a non-admin user directly."""
    import time as _t
    from app.auth import hash_password
    from app.main import app

    await app.state.db.execute(
        "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
        (username, hash_password(password), "user", int(_t.time())),
    )
    await app.state.db.commit()


def _native_handler(request):
    import httpx as _h
    if request.url.path == "/api/tags":
        return _h.Response(
            200,
            json={
                "models": [
                    {"name": "llama3.2", "size": 4_700_000_000, "modified_at": "2026-05-28T10:00:00Z", "digest": "abc"},
                    {"name": "nomic-embed-text", "size": 270_000_000, "modified_at": "2026-05-28T10:00:00Z", "digest": "def"},
                ]
            },
        )
    if request.url.path == "/api/pull":
        chunks = [
            json.dumps({"status": "pulling manifest"}).encode() + b"\n",
            json.dumps({"status": "pulling 1234", "completed": 50, "total": 100}).encode() + b"\n",
            json.dumps({"status": "success"}).encode() + b"\n",
        ]
        return _h.Response(200, content=b"".join(chunks))
    if request.url.path == "/api/delete":
        return _h.Response(200, json={})
    return _h.Response(404)


def _patch_httpx(monkeypatch):
    import httpx as _h

    OriginalAsyncClient = _h.AsyncClient

    class PatchedAsyncClient(OriginalAsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("transport", _h.MockTransport(_native_handler))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(_h, "AsyncClient", PatchedAsyncClient)


async def test_list_installed_models(client, monkeypatch):
    _patch_httpx(monkeypatch)
    await _signup(client)
    r = await client.get("/api/admin/models")
    assert r.status_code == 200
    names = [m["name"] for m in r.json()]
    assert "llama3.2" in names
    assert "nomic-embed-text" in names


async def test_pull_model_streams_progress(client, monkeypatch):
    _patch_httpx(monkeypatch)
    await _signup(client)
    events: list[dict] = []
    async with client.stream(
        "POST", "/api/admin/models/pull", json={"name": "llama3.2"}
    ) as r:
        assert r.status_code == 200
        async for line in r.aiter_lines():
            if line.strip():
                events.append(json.loads(line))
    statuses = [e.get("status") for e in events]
    assert "pulling manifest" in statuses
    assert "success" in statuses


async def test_delete_model(client, monkeypatch):
    _patch_httpx(monkeypatch)
    await _signup(client)
    r = await client.delete("/api/admin/models?name=llama3.2")
    assert r.status_code == 204


async def test_admin_only(client, monkeypatch):
    _patch_httpx(monkeypatch)
    await _signup(client)  # alice = admin
    await _signup_user(client, "bob", "hunter22hunter")
    await client.post("/api/auth/logout")
    await client.post(
        "/api/auth/login", json={"username": "bob", "password": "hunter22hunter"}
    )
    assert (await client.get("/api/admin/models")).status_code == 403
    assert (
        await client.post("/api/admin/models/pull", json={"name": "llama3.2"})
    ).status_code == 403
