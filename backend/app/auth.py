import secrets
import time
from pathlib import Path
from typing import Any

import aiosqlite
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, Field

from .config import settings

SESSION_COOKIE = "fw_session"

_ph = PasswordHasher()


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


def issue_session(user_id: int, username: str, role: str) -> str:
    return _serializer.dumps({"uid": user_id, "u": username, "r": role})


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
        "SELECT id, username, role FROM users WHERE id = ?", (payload["uid"],)
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="user not found")
    return {"id": row[0], "username": row[1], "role": row[2]}


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_max_age,
        httponly=True,
        samesite="lax",
        secure=False,  # set True behind HTTPS in production
        path="/",
    )


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
                "SELECT id, username, role FROM users WHERE id = ?", (payload["uid"],)
            )
            row = await cur.fetchone()
            if row:
                user = UserOut(id=row[0], username=row[1], role=row[2])
    return AuthStatus(user=user, setup_required=setup_required)


@router.post("/setup", response_model=UserOut)
async def setup_endpoint(body: SetupBody, request: Request, response: Response) -> UserOut:
    db = _db(request)
    if await _user_count(db) > 0:
        raise HTTPException(status_code=409, detail="setup already completed")
    now = int(time.time())
    cur = await db.execute(
        "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
        (body.username, hash_password(body.password), "admin", now),
    )
    uid = cur.lastrowid
    # Claim any pre-existing orphan conversations (from before auth was added).
    await db.execute(
        "UPDATE conversations SET user_id = ? WHERE user_id IS NULL", (uid,)
    )
    await db.commit()
    token = issue_session(uid, body.username, "admin")
    _set_cookie(response, token)
    return UserOut(id=uid, username=body.username, role="admin")


@router.post("/login", response_model=UserOut)
async def login_endpoint(body: LoginBody, request: Request, response: Response) -> UserOut:
    db = _db(request)
    cur = await db.execute(
        "SELECT id, password_hash, role FROM users WHERE username = ?", (body.username,)
    )
    row = await cur.fetchone()
    if not row or not verify_password(row[1], body.password):
        raise HTTPException(status_code=401, detail="invalid credentials")
    uid, _, role = row
    token = issue_session(uid, body.username, role)
    _set_cookie(response, token)
    return UserOut(id=uid, username=body.username, role=role)


@router.post("/logout", status_code=204)
async def logout_endpoint(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=UserOut)
async def me_endpoint(user: dict = Depends(current_user)) -> UserOut:
    return UserOut(**user)
