import asyncio
import secrets
import time
from pathlib import Path
from typing import Any

import aiosqlite
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, Field

from .config import oidc_enabled, settings
from .webhooks import notify_signup

SESSION_COOKIE = "fw_session"

_ph = PasswordHasher()

# The "first user -> admin" decision is guarded by a single lock shared by /setup
# and OIDC sign-in, so two paths (or two replicas) can't each mint a first admin.
# The asyncio.Lock serializes within a replica; pair it with
# db.advisory_lock(PROVISION_LOCK_KEY) for the cross-replica (Postgres) guarantee
# — a no-op on SQLite, where the single process + this lock already suffice.
provision_lock = asyncio.Lock()
PROVISION_LOCK_KEY = 0x66775F6F6964  # "fw_oid"


def _resolve_secret() -> str:
    if settings.secret_key:
        return settings.secret_key
    p = Path(settings.secret_key_path)
    if p.exists():
        return p.read_text().strip()
    secret = secrets.token_urlsafe(48)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(secret)
    try:
        p.chmod(0o600)
    except OSError:
        pass
    return secret


_secret = _resolve_secret()
_serializer = URLSafeTimedSerializer(_secret, salt="free-webui-session")


def issue_session(user_id: int, username: str, role: str, token_version: int = 0) -> str:
    return _serializer.dumps(
        {"uid": user_id, "u": username, "r": role, "tv": token_version}
    )


def read_session(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    try:
        return _serializer.loads(token, max_age=settings.session_max_age)
    except (BadSignature, SignatureExpired):
        return None


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except VerifyMismatchError:
        return False


router = APIRouter(prefix="/api/auth", tags=["auth"])


class SetupBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=6, max_length=256)


class LoginBody(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: int
    username: str
    role: str


class AuthStatus(BaseModel):
    user: UserOut | None = None
    setup_required: bool
    oidc_enabled: bool = False
    oidc_name: str = "SSO"


def _db(request: Request) -> aiosqlite.Connection:
    return request.app.state.db


async def _user_count(db: aiosqlite.Connection) -> int:
    cur = await db.execute("SELECT COUNT(*) FROM users")
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def current_user(request: Request) -> dict[str, Any]:
    """Dependency: returns the active user dict or raises 401."""
    raw = request.cookies.get(SESSION_COOKIE)
    payload = read_session(raw)
    if not payload:
        raise HTTPException(status_code=401, detail="not authenticated")
    db = _db(request)
    cur = await db.execute(
        "SELECT id, username, role, token_version FROM users WHERE id = ?", (payload["uid"],)
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="user not found")
    if int(payload.get("tv", 0)) != int(row[3]):
        # The session was revoked (password reset / logout-everywhere).
        raise HTTPException(status_code=401, detail="session expired")
    return {"id": row[0], "username": row[1], "role": row[2]}


async def require_admin(user: dict = Depends(current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    return user


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_max_age,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,  # set FREE_WEBUI_COOKIE_SECURE=true behind HTTPS
        path="/",
    )


# In-process login throttle: maps (client IP, username) -> recent attempt
# timestamps. Best-effort and per-worker (a shared store is a follow-up), but
# enough to blunt credential stuffing on a single instance.
_login_attempts: dict[str, list[float]] = {}

# Optional shared store for the limiter (a redis.asyncio client), configured at
# startup when FREE_WEBUI_REDIS_URL is set. With it the throttle is GLOBAL across
# replicas (a fixed-window INCR+EXPIRE counter); without it, the per-worker
# in-process sliding window below — enough for a single instance.
_rate_redis = None


def configure_rate_limiter(redis_client) -> None:
    global _rate_redis
    _rate_redis = redis_client


def _check_login_rate_local(key: str, limit: int, window: float) -> None:
    now = time.time()
    recent = [t for t in _login_attempts.get(key, []) if now - t < window]
    if len(recent) >= limit:
        _login_attempts[key] = recent
        raise HTTPException(
            status_code=429, detail="too many login attempts; please wait and retry"
        )
    recent.append(now)
    _login_attempts[key] = recent


async def _check_login_rate(request: Request, username: str) -> None:
    limit = settings.login_rate_limit
    if limit <= 0:
        return
    window = settings.login_rate_window_seconds
    ip = request.client.host if request.client else "?"
    key = f"{ip}\x00{username}"
    if _rate_redis is not None:
        try:
            rkey = f"fw:loginrate:{key}"
            n = await _rate_redis.incr(rkey)
            if n == 1:
                await _rate_redis.expire(rkey, int(window) or 1)
            if n > limit:
                raise HTTPException(
                    status_code=429, detail="too many login attempts; please wait and retry"
                )
            return
        except HTTPException:
            raise
        except Exception:
            # Redis hiccup must never lock everyone out — fall back to the local
            # window so logins keep working (best-effort throttle).
            pass
    _check_login_rate_local(key, limit, window)


@router.get("/status", response_model=AuthStatus)
async def status_endpoint(request: Request) -> AuthStatus:
    db = _db(request)
    n = await _user_count(db)
    setup_required = n == 0
    user = None
    if not setup_required:
        raw = request.cookies.get(SESSION_COOKIE)
        payload = read_session(raw)
        if payload:
            cur = await db.execute(
                "SELECT id, username, role, token_version FROM users WHERE id = ?",
                (payload["uid"],),
            )
            row = await cur.fetchone()
            if row and int(payload.get("tv", 0)) == int(row[3]):
                user = UserOut(id=row[0], username=row[1], role=row[2])
    return AuthStatus(
        user=user,
        setup_required=setup_required,
        oidc_enabled=oidc_enabled(),
        oidc_name=settings.oidc_provider_name,
    )


@router.post("/setup", response_model=UserOut)
async def setup_endpoint(body: SetupBody, request: Request, response: Response) -> UserOut:
    db = _db(request)
    # Same first-admin lock OIDC uses, so a concurrent /setup + OIDC first sign-in
    # (or two /setup calls across replicas) can't each create a first admin: the
    # count check + insert run as one critical section, cluster-wide on Postgres.
    async with provision_lock, db.advisory_lock(PROVISION_LOCK_KEY):
        if await _user_count(db) > 0:
            raise HTTPException(status_code=409, detail="setup already completed")
        now = int(time.time())
        uid = await db.insert(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
            (body.username, hash_password(body.password), "admin", now),
        )
        # Claim any pre-existing orphan conversations (from before auth was added).
        await db.execute(
            "UPDATE conversations SET user_id = ? WHERE user_id IS NULL", (uid,)
        )
        await db.commit()
    await notify_signup(body.username, "admin", "setup")
    token = issue_session(uid, body.username, "admin", 0)
    _set_cookie(response, token)
    return UserOut(id=uid, username=body.username, role="admin")


@router.post("/login", response_model=UserOut)
async def login_endpoint(body: LoginBody, request: Request, response: Response) -> UserOut:
    await _check_login_rate(request, body.username)
    db = _db(request)
    cur = await db.execute(
        "SELECT id, password_hash, role, token_version FROM users WHERE username = ?",
        (body.username,),
    )
    row = await cur.fetchone()
    if not row or not verify_password(row[1], body.password):
        raise HTTPException(status_code=401, detail="invalid credentials")
    uid, _, role, tv = row
    token = issue_session(uid, body.username, role, tv)
    _set_cookie(response, token)
    return UserOut(id=uid, username=body.username, role=role)


@router.post("/logout", status_code=204)
async def logout_endpoint(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.post("/logout_all", status_code=204)
async def logout_all_endpoint(
    request: Request, response: Response, user: dict = Depends(current_user)
) -> None:
    """Revoke every session for the current user (bump token_version), so all
    other devices/cookies are signed out on their next request."""
    db = _db(request)
    await db.execute(
        "UPDATE users SET token_version = token_version + 1 WHERE id = ?", (user["id"],)
    )
    await db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=UserOut)
async def me_endpoint(user: dict = Depends(current_user)) -> UserOut:
    return UserOut(**user)
