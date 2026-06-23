"""Outgoing signup webhook: payload shape, disabled no-op, and that it fires on
the user-creation paths without breaking signup when the endpoint is down."""
import httpx


def _capture(monkeypatch, sink):
    """Route the webhook's fresh httpx.AsyncClient through a MockTransport that
    records each POST (url + parsed JSON body) into `sink`."""
    import json as _json

    orig = httpx.AsyncClient

    def handler(req: httpx.Request) -> httpx.Response:
        sink.append({"url": str(req.url), "json": _json.loads(req.content)})
        return httpx.Response(200, json={"ok": True})

    class Patched(orig):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", Patched)


async def test_notify_signup_payload(monkeypatch):
    from app import webhooks

    monkeypatch.setattr(webhooks.settings, "webhook_url", "http://hook.test/x")
    monkeypatch.setattr(webhooks.settings, "instance_name", "Acme")
    sent: list = []
    _capture(monkeypatch, sent)

    await webhooks.notify_signup("bob", "user", "admin")
    assert len(sent) == 1
    p = sent[0]["json"]
    assert p["event"] == "user.signup"
    assert p["username"] == "bob" and p["role"] == "user" and p["via"] == "admin"
    assert p["instance"] == "Acme"
    # Slack `text` + Discord `content` both present and human-readable
    assert "bob" in p["text"] and p["text"] == p["content"]


async def test_notify_signup_disabled_is_noop(monkeypatch):
    from app import webhooks

    monkeypatch.setattr(webhooks.settings, "webhook_url", "")
    sent: list = []
    _capture(monkeypatch, sent)
    await webhooks.notify_signup("bob", "user", "admin")
    assert sent == []  # unconfigured -> no POST


async def test_notify_signup_swallows_errors(monkeypatch):
    from app import webhooks

    monkeypatch.setattr(webhooks.settings, "webhook_url", "http://hook.test/x")

    orig = httpx.AsyncClient

    def boom(req):
        raise httpx.ConnectError("down")

    class Patched(orig):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(boom)
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", Patched)
    # must not raise even though the endpoint errors
    await webhooks.notify_signup("bob", "user", "admin")


async def test_setup_fires_webhook(client, monkeypatch):
    from app import webhooks

    monkeypatch.setattr(webhooks.settings, "webhook_url", "http://hook.test/x")
    sent: list = []
    _capture(monkeypatch, sent)
    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})
    assert len(sent) == 1
    assert sent[0]["json"]["username"] == "alice" and sent[0]["json"]["via"] == "setup"


async def test_admin_create_user_fires_webhook(client, monkeypatch):
    from app import webhooks

    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})
    monkeypatch.setattr(webhooks.settings, "webhook_url", "http://hook.test/x")
    sent: list = []
    _capture(monkeypatch, sent)
    await client.post(
        "/api/admin/users", json={"username": "bob", "password": "passpass", "role": "user"}
    )
    assert len(sent) == 1
    assert sent[0]["json"]["username"] == "bob" and sent[0]["json"]["via"] == "admin"
