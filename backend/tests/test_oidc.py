"""OIDC SSO: discovery -> authorize redirect -> callback -> provision/link/login."""
from urllib.parse import parse_qs, urlparse

import httpx


def _oidc_transport(claims: dict) -> httpx.MockTransport:
    async def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={
                "issuer": "http://oidc.test",
                "authorization_endpoint": "http://oidc.test/authorize",
                "token_endpoint": "http://oidc.test/token",
                "userinfo_endpoint": "http://oidc.test/userinfo",
            })
        if path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "at-123", "token_type": "Bearer"})
        if path.endswith("/userinfo"):
            return httpx.Response(200, json=claims)
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def _enable(monkeypatch, claims, *, allow_signup=True, name="SSO"):
    from app import oidc
    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "oidc_issuer", "http://oidc.test")
    monkeypatch.setattr(cfg, "oidc_client_id", "cid")
    monkeypatch.setattr(cfg, "oidc_client_secret", "csecret")
    monkeypatch.setattr(cfg, "oidc_redirect_uri", "http://testserver/api/auth/oidc/callback")
    monkeypatch.setattr(cfg, "oidc_allow_signup", allow_signup)
    monkeypatch.setattr(cfg, "oidc_provider_name", name)
    monkeypatch.setattr(cfg, "oidc_insecure_transport", True)  # the mock uses http
    monkeypatch.setattr(oidc, "_client", lambda: httpx.AsyncClient(transport=_oidc_transport(claims)))
    oidc._discovery_cache.clear()


async def _login_get_state(client) -> str:
    r = await client.get("/api/auth/oidc/login")
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("http://oidc.test/authorize")
    return parse_qs(urlparse(loc).query)["state"][0]


async def test_oidc_login_404_when_disabled(client):
    assert (await client.get("/api/auth/oidc/login")).status_code == 404


async def test_auth_status_reports_oidc(client, monkeypatch):
    _enable(monkeypatch, {"sub": "s"}, name="Okta")
    s = (await client.get("/api/auth/status")).json()
    assert s["oidc_enabled"] is True and s["oidc_name"] == "Okta"


async def test_oidc_full_login_provisions_first_user_as_admin(client, monkeypatch):
    _enable(monkeypatch, {"sub": "abc", "email": "x@y.com", "email_verified": True, "name": "X"})
    state = await _login_get_state(client)

    cb = await client.get(f"/api/auth/oidc/callback?code=fakecode&state={state}")
    assert cb.status_code == 302
    assert cb.headers["location"] == "/"

    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "x@y.com"
    assert me.json()["role"] == "admin"  # first user provisioned as admin


async def test_oidc_callback_bad_state_redirects_to_login(client, monkeypatch):
    _enable(monkeypatch, {"sub": "abc", "email": "x@y.com"})
    # no /login first -> no valid state cookie; failure bounces back to login
    r = await client.get("/api/auth/oidc/callback?code=x&state=bogus")
    assert r.status_code == 302
    assert r.headers["location"].startswith("/login?sso_error=")


async def test_oidc_signup_disabled_redirects_to_login(client, monkeypatch):
    _enable(monkeypatch, {"sub": "abc", "email": "x@y.com", "email_verified": True}, allow_signup=False)
    state = await _login_get_state(client)
    cb = await client.get(f"/api/auth/oidc/callback?code=x&state={state}")
    assert cb.status_code == 302
    assert "/login?sso_error=" in cb.headers["location"]


async def test_oidc_requires_https_by_default(client, monkeypatch):
    from app import oidc
    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "oidc_issuer", "http://oidc.test")
    monkeypatch.setattr(cfg, "oidc_client_id", "cid")
    monkeypatch.setattr(cfg, "oidc_client_secret", "csecret")
    # oidc_insecure_transport stays False
    monkeypatch.setattr(oidc, "_client", lambda: httpx.AsyncClient(transport=_oidc_transport({"sub": "s"})))
    oidc._discovery_cache.clear()
    r = await client.get("/api/auth/oidc/login")
    assert r.status_code == 502  # cleartext http issuer refused by default


async def test_oidc_links_existing_local_account(client, monkeypatch):
    from app.main import app

    # a local account whose username is the SSO email
    await client.post("/api/auth/setup", json={"username": "x@y.com", "password": "hunter22hunter"})
    _enable(monkeypatch, {"sub": "abc", "email": "x@y.com", "email_verified": True, "name": "X"})

    state = await _login_get_state(client)
    cb = await client.get(f"/api/auth/oidc/callback?code=x&state={state}")
    assert cb.status_code == 302

    # linked to the SAME user (no duplicate), and oidc_sub now set
    n = (await (await app.state.db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
    assert n == 1
    sub = (await (await app.state.db.execute("SELECT oidc_sub FROM users WHERE username='x@y.com'")).fetchone())[0]
    assert sub == "abc"


async def test_oidc_unverified_email_does_not_hijack_account(client, monkeypatch):
    from app.main import app

    # an existing local admin whose username is an email
    await client.post("/api/auth/setup", json={"username": "admin@corp.com", "password": "hunter22hunter"})
    # an attacker's SSO identity claims that email but UNVERIFIED
    _enable(monkeypatch, {"sub": "attacker", "email": "admin@corp.com", "email_verified": False})

    state = await _login_get_state(client)
    cb = await client.get(f"/api/auth/oidc/callback?code=x&state={state}")
    assert cb.status_code == 302  # a separate, non-privileged account is created

    # the admin account was NOT linked or taken over
    sub = (await (await app.state.db.execute("SELECT oidc_sub FROM users WHERE username='admin@corp.com'")).fetchone())[0]
    assert sub is None
    cur = await app.state.db.execute("SELECT username, role FROM users WHERE oidc_sub = 'attacker'")
    uname, role = await cur.fetchone()
    assert uname != "admin@corp.com" and role == "user"
