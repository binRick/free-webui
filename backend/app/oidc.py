"""OIDC / OAuth2 single sign-on (authorization-code flow).

No JWT crypto and no extra dependency: we exchange the authorization code
server-side over TLS and read the user's claims from the provider's userinfo
endpoint, so the claims are trustworthy without local signature verification.
CSRF is handled with a signed, short-lived state cookie.

Enable by setting oidc_issuer + oidc_client_id + oidc_client_secret. The
redirect_uri must point at this backend's /api/auth/oidc/callback.
"""
import asyncio
import re
import secrets
import time
from urllib.parse import quote, urlencode

import aiosqlite
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .auth import _secret, _set_cookie, hash_password, issue_session
from .config import oidc_enabled, settings

router = APIRouter(prefix="/api/auth/oidc", tags=["auth"])

_STATE_COOKIE = "fw_oidc_state"
_STATE_MAX_AGE = 600  # 10 minutes to complete the round trip
_state_serializer = URLSafeTimedSerializer(_secret, salt="free-webui-oidc-state")

_discovery_cache: dict = {}
# Serialize user provisioning so a "first user -> admin" decision can't race two
# concurrent first sign-ins (single shared connection => one worker per process).
_provision_lock = asyncio.Lock()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=15.0)


def _require_https(url: str, what: str) -> None:
    """Refuse cleartext OIDC endpoints unless explicitly opted in."""
    if settings.oidc_insecure_transport:
        return
    if not (url or "").lower().startswith("https://"):
        raise HTTPException(status_code=502, detail=f"oidc {what} must be https")


def _json_or_502(r: httpx.Response, what: str) -> dict:
    try:
        body = r.json()
    except ValueError:
        raise HTTPException(status_code=502, detail=f"oidc {what} returned non-JSON")
    return body if isinstance(body, dict) else {}


def _verified_email(claims: dict) -> str | None:
    """The normalized email iff the IdP asserts it is verified (bool True or the
    string 'true'). An unverified email is ignored entirely (anti-takeover)."""
    raw = claims.get("email_verified")
    verified = raw is True or str(raw).strip().lower() == "true"
    email = (claims.get("email") or "").strip().lower()
    return email if (verified and email) else None


def _sanitize_username(base: str, sub: str) -> str:
    cleaned = re.sub(r"[\x00-\x1f\s]+", " ", base or "").strip()[:64].strip()
    return cleaned or f"oidc-{sub[:24]}"


async def _discover() -> dict:
    """Fetch (and cache) the provider's OpenID configuration document."""
    if _discovery_cache.get("issuer") == settings.oidc_issuer and "doc" in _discovery_cache:
        return _discovery_cache["doc"]
    url = settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
    _require_https(url, "issuer")
    try:
        async with _client() as c:
            r = await c.get(url)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"oidc discovery unreachable: {e}")
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"oidc discovery failed ({r.status_code})")
    doc = _json_or_502(r, "discovery")
    # The discovered endpoints must be https too (a downgraded endpoint in a
    # tampered doc would otherwise leak the client_secret / access token).
    _require_https(doc.get("token_endpoint", ""), "token_endpoint")
    _require_https(doc.get("userinfo_endpoint", ""), "userinfo_endpoint")
    _discovery_cache.clear()
    _discovery_cache.update(issuer=settings.oidc_issuer, doc=doc)
    return doc


async def _user_by_sub(db: aiosqlite.Connection, sub: str) -> dict | None:
    cur = await db.execute(
        "SELECT id, username, role, token_version FROM users WHERE oidc_sub = ?", (sub,)
    )
    row = await cur.fetchone()
    if row:
        return {"id": row[0], "username": row[1], "role": row[2], "token_version": row[3]}
    return None


async def _find_or_create_user(db: aiosqlite.Connection, sub: str, claims: dict) -> dict | None:
    """Return the user for this OIDC identity, linking or provisioning as needed.
    Returns None when the user is unknown and signup is disabled."""
    found = await _user_by_sub(db, sub)
    if found:
        return found

    verified_email = _verified_email(claims)

    # Link to an existing local account whose username matches the verified email
    # (case-insensitively) and isn't already linked to a different identity.
    if verified_email:
        cur = await db.execute(
            "SELECT id, username, role, token_version, oidc_sub FROM users "
            "WHERE username = ? COLLATE NOCASE",
            (verified_email,),
        )
        existing = await cur.fetchone()
        if existing and existing[4] is None:
            try:
                await db.execute("UPDATE users SET oidc_sub = ? WHERE id = ?", (sub, existing[0]))
                await db.commit()
            except aiosqlite.IntegrityError:
                await db.rollback()
            return {"id": existing[0], "username": existing[1], "role": existing[2], "token_version": existing[3]}

    if not settings.oidc_allow_signup:
        return None

    # Provision under a lock so the "first user -> admin" decision and the INSERT
    # are atomic against a concurrent first sign-in.
    async with _provision_lock:
        found = await _user_by_sub(db, sub)  # a racer may have created it
        if found:
            return found

        cur = await db.execute("SELECT COUNT(*) FROM users")
        is_first = (await cur.fetchone())[0] == 0
        admin_emails = {e.lower() for e in settings.oidc_admin_emails}
        role = "admin" if is_first or (verified_email and verified_email in admin_emails) else "user"

        base = _sanitize_username(
            verified_email or claims.get("preferred_username") or f"oidc-{sub[:12]}", sub
        )
        uname, i = base, 1
        while True:
            cur = await db.execute("SELECT 1 FROM users WHERE username = ? COLLATE NOCASE", (uname,))
            if not await cur.fetchone():
                break
            i += 1
            uname = f"{base}-{i}"

        now = int(time.time())
        # OIDC users get a random, unguessable password hash (local login can't match).
        try:
            cur = await db.execute(
                "INSERT INTO users (username, password_hash, role, oidc_sub, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (uname, hash_password(secrets.token_urlsafe(32)), role, sub, now),
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            await db.rollback()
            found = await _user_by_sub(db, sub)
            if found:
                return found
            raise
        return {"id": cur.lastrowid, "username": uname, "role": role, "token_version": 0}


@router.get("/login")
async def oidc_login(request: Request):
    if not oidc_enabled():
        raise HTTPException(status_code=404, detail="oidc not enabled")
    doc = await _discover()
    auth_ep = doc.get("authorization_endpoint")
    if not auth_ep:
        raise HTTPException(status_code=502, detail="oidc discovery missing authorization_endpoint")
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(16)
    params = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": settings.oidc_redirect_uri,
        "scope": settings.oidc_scopes,
        "state": state,
        "nonce": nonce,
    }
    url = auth_ep + ("&" if "?" in auth_ep else "?") + urlencode(params)
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(
        _STATE_COOKIE,
        _state_serializer.dumps({"state": state, "nonce": nonce}),
        max_age=_STATE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )
    return resp


def _login_redirect(reason: str) -> RedirectResponse:
    """Send the browser back to the SPA login with an error, rather than showing
    a raw JSON error page mid-flow."""
    resp = RedirectResponse(f"/login?sso_error={quote(reason)}", status_code=302)
    resp.delete_cookie(_STATE_COOKIE, path="/")
    return resp


@router.get("/callback")
async def oidc_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    if not oidc_enabled():
        raise HTTPException(status_code=404, detail="oidc not enabled")
    try:
        return await _complete_callback(request, code, state, error)
    except HTTPException as e:
        # The browser is mid-flow; bounce back to login with a message instead
        # of a raw JSON error page.
        return _login_redirect(str(e.detail))


async def _complete_callback(
    request: Request, code: str | None, state: str | None, error: str | None
) -> RedirectResponse:
    if error:
        raise HTTPException(status_code=400, detail=f"provider error: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="missing code or state")

    raw = request.cookies.get(_STATE_COOKIE)
    try:
        saved = _state_serializer.loads(raw, max_age=_STATE_MAX_AGE) if raw else None
    except (BadSignature, SignatureExpired):
        saved = None
    if not saved or not secrets.compare_digest(saved.get("state", ""), state):
        raise HTTPException(status_code=400, detail="invalid or expired sign-in state")

    doc = await _discover()
    try:
        async with _client() as c:
            tr = await c.post(
                doc["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": settings.oidc_redirect_uri,
                    "client_id": settings.oidc_client_id,
                    "client_secret": settings.oidc_client_secret,
                },
                headers={"accept": "application/json"},
            )
    except (httpx.HTTPError, KeyError) as e:
        raise HTTPException(status_code=502, detail=f"token exchange failed: {e}")
    if tr.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"token exchange failed ({tr.status_code})")
    access_token = _json_or_502(tr, "token").get("access_token")
    if not access_token:
        raise HTTPException(status_code=502, detail="token response missing access_token")

    try:
        async with _client() as c:
            ur = await c.get(
                doc["userinfo_endpoint"], headers={"authorization": f"Bearer {access_token}"}
            )
    except (httpx.HTTPError, KeyError) as e:
        raise HTTPException(status_code=502, detail=f"userinfo failed: {e}")
    if ur.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"userinfo failed ({ur.status_code})")
    claims = _json_or_502(ur, "userinfo")
    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status_code=502, detail="userinfo missing sub")

    user = await _find_or_create_user(request.app.state.db, str(sub), claims)
    if user is None:
        raise HTTPException(status_code=403, detail="sign-up via SSO is disabled")

    token = issue_session(user["id"], user["username"], user["role"], user["token_version"])
    resp = RedirectResponse(settings.oidc_post_login_redirect, status_code=302)
    _set_cookie(resp, token)
    resp.delete_cookie(_STATE_COOKIE, path="/")
    return resp
