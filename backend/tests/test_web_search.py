import json


async def _signup(client):
    await client.post(
        "/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"}
    )


async def test_status_disabled_by_default(client):
    await _signup(client)
    r = await client.get("/api/web_search/status")
    assert r.status_code == 200
    assert r.json() == {"available": False, "url": None}


async def test_status_available_when_configured(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "searxng_url", "http://searxng.local")
    await _signup(client)
    r = await client.get("/api/web_search/status")
    assert r.json() == {"available": True, "url": "http://searxng.local"}


async def test_patch_toggles_web_search(client):
    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    patched = await client.patch(
        f"/api/conversations/{cid}", json={"web_search": True}
    )
    assert patched.status_code == 200
    assert patched.json()["web_search"] is True


async def test_message_includes_web_results_when_enabled(client, monkeypatch):
    """When web_search is on and SearXNG returns results, those results
    must show up in the upstream chat-completions payload as a system
    message containing the URL and snippet."""
    import httpx
    from app.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "searxng_url", "http://searxng.test")
    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    await client.patch(f"/api/conversations/{cid}", json={"web_search": True})

    captured: dict = {}

    async def fake_upstream(request: httpx.Request) -> httpx.Response:
        from tests.conftest import _fake_chat_stream, _fake_handler
        if request.url.host == "searxng.test":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Wikipedia: Capybara",
                            "url": "https://en.wikipedia.org/wiki/Capybara",
                            "content": "Largest living rodent. Native to South America.",
                        }
                    ]
                },
            )
        if request.url.path.endswith("/chat/completions"):
            captured["payload"] = json.loads(request.content)
            return _fake_chat_stream(captured["payload"])
        return await _fake_handler(request)

    # Patch BOTH the app's persistent client (for /chat/completions) and the
    # web_search module's per-call client (which is constructed via
    # httpx.AsyncClient()).
    app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(fake_upstream), base_url="http://upstream/v1"
    )
    OriginalAsyncClient = httpx.AsyncClient

    class PatchedAsyncClient(OriginalAsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("transport", httpx.MockTransport(fake_upstream))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedAsyncClient)

    async with client.stream(
        "POST",
        f"/api/conversations/{cid}/messages",
        json={"content": "what is a capybara?"},
    ) as r:
        async for _ in r.aiter_lines():
            pass

    payload = captured["payload"]
    systems = "\n".join(m["content"] for m in payload["messages"] if m["role"] == "system")
    assert "Capybara" in systems
    assert "wikipedia.org/wiki/Capybara" in systems
