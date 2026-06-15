"""End-to-end WebSocket tests for real-time channels, driven by Starlette's
TestClient (the async httpx fixture can't open a real WebSocket)."""
import importlib
import os
import sys
import tempfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


@pytest.fixture
def ws_client():
    tmp = Path(tempfile.mkdtemp(prefix="fw-ws-test-"))
    os.environ["FREE_WEBUI_DB_PATH"] = str(tmp / "test.db")
    os.environ["FREE_WEBUI_SECRET_KEY"] = "test-secret-for-ws-tests"
    os.environ["FREE_WEBUI_SECRET_KEY_PATH"] = str(tmp / "secret.key")
    for mod in [m for m in list(sys.modules) if m == "app" or m.startswith("app.")]:
        del sys.modules[mod]
    config = importlib.import_module("app.config")
    config.settings.db_path = str(tmp / "test.db")
    config.settings.secret_key = "test-secret-for-ws-tests"
    config.settings.secret_key_path = str(tmp / "secret.key")
    config.settings.ssrf_protection = False
    config.settings.login_rate_limit = 0

    from app.main import app  # noqa: WPS433

    # TestClient's context manager runs the app lifespan (opens the DB).
    with TestClient(app) as c:
        yield c


def _setup_admin(c):
    c.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})


def _new_channel(c, name="general"):
    return c.post("/api/channels", json={"name": name}).json()["id"]


def test_ws_receives_rest_broadcast(ws_client):
    _setup_admin(ws_client)
    cid = _new_channel(ws_client)
    with ws_client.websocket_connect(f"/api/channels/{cid}/ws") as ws:
        join = ws.receive_json()
        assert join["type"] == "presence" and join["event"] == "join"
        assert join["online"] == 1
        # a REST post is broadcast live to the socket
        ws_client.post(f"/api/channels/{cid}/messages", json={"content": "hello"})
        msg = ws.receive_json()
        assert msg["type"] == "message"
        assert msg["content"] == "hello"
        assert msg["username"] == "alice"
        assert isinstance(msg["id"], int)


def test_ws_inbound_message_and_typing(ws_client):
    _setup_admin(ws_client)
    cid = _new_channel(ws_client)
    with ws_client.websocket_connect(f"/api/channels/{cid}/ws") as ws:
        ws.receive_json()  # presence join
        ws.send_json({"type": "message", "content": "via ws"})
        echoed = ws.receive_json()
        assert echoed["type"] == "message" and echoed["content"] == "via ws"
        # persisted, so it shows up in history
        history = ws_client.get(f"/api/channels/{cid}/messages").json()
        assert [m["content"] for m in history] == ["via ws"]

        ws.send_json({"type": "typing"})
        typing = ws.receive_json()
        assert typing["type"] == "typing" and typing["username"] == "alice"


def test_ws_rejects_unauthenticated(ws_client):
    # No setup / no session cookie -> the server closes before accept.
    with pytest.raises(WebSocketDisconnect):
        with ws_client.websocket_connect("/api/channels/anything/ws"):
            pass


def test_ws_rejects_unknown_channel(ws_client):
    _setup_admin(ws_client)  # authenticated, but the channel doesn't exist
    with pytest.raises(WebSocketDisconnect):
        with ws_client.websocket_connect("/api/channels/does-not-exist/ws"):
            pass


def test_ws_session_revocation_closes_socket(ws_client):
    """A session revoked (logout-everywhere bumps token_version) after connect
    must not keep posting on the live socket — the per-message re-validation
    closes it and the post is dropped."""
    _setup_admin(ws_client)
    cid = _new_channel(ws_client)
    with ws_client.websocket_connect(f"/api/channels/{cid}/ws") as ws:
        ws.receive_json()  # presence join
        # revoke every session for this user
        assert ws_client.post("/api/auth/logout_all").status_code in (200, 204)
        # the stale socket tries to post -> server re-validates, rejects, closes
        ws.send_json({"type": "message", "content": "ghost"})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    # the post was never persisted
    ws_client.post("/api/auth/login", json={"username": "alice", "password": "hunter22hunter"})
    history = ws_client.get(f"/api/channels/{cid}/messages").json()
    assert all(m["content"] != "ghost" for m in history)


def test_ws_connection_cap(ws_client):
    from app.config import settings

    _setup_admin(ws_client)
    cid = _new_channel(ws_client)
    settings.channel_max_connections_per_user = 2
    with ws_client.websocket_connect(f"/api/channels/{cid}/ws") as ws1:
        ws1.receive_json()
        with ws_client.websocket_connect(f"/api/channels/{cid}/ws") as ws2:
            ws2.receive_json()
            # a third concurrent socket for the same user exceeds the cap
            with pytest.raises(WebSocketDisconnect):
                with ws_client.websocket_connect(f"/api/channels/{cid}/ws"):
                    pass
