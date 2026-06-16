import time

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .audit import record
from .auth import current_user, hash_password, require_admin

router = APIRouter(
    prefix="/api/admin/users",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


class UserListed(BaseModel):
    id: int
    username: str
    role: str
    created_at: int


class CreateUserBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=6, max_length=256)
    role: str = Field(default="user", pattern="^(admin|user)$")


class UpdateUserBody(BaseModel):
    role: str | None = Field(default=None, pattern="^(admin|user)$")
    password: str | None = Field(default=None, min_length=6, max_length=256)


def _db(request: Request) -> aiosqlite.Connection:
    return request.app.state.db


@router.get("", response_model=list[UserListed])
async def list_users(request: Request):
    db = _db(request)
    cur = await db.execute(
        "SELECT id, username, role, created_at FROM users ORDER BY id"
    )
    return [
        UserListed(id=r[0], username=r[1], role=r[2], created_at=r[3])
        for r in await cur.fetchall()
    ]


@router.post("", response_model=UserListed)
async def create_user(body: CreateUserBody, request: Request, me: dict = Depends(current_user)):
    db = _db(request)
    cur = await db.execute(
        "SELECT 1 FROM users WHERE username = ?", (body.username,)
    )
    if await cur.fetchone():
        raise HTTPException(status_code=409, detail="username already exists")
    now = int(time.time())
    new_id = await db.insert(
        "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
        (body.username, hash_password(body.password), body.role, now),
    )
    await db.commit()
    await record(db, me, "user.create", f"username={body.username} role={body.role}")
    return UserListed(
        id=new_id, username=body.username, role=body.role, created_at=now
    )


@router.patch("/{uid}", response_model=UserListed)
async def update_user(
    uid: int,
    body: UpdateUserBody,
    request: Request,
    me: dict = Depends(current_user),
):
    db = _db(request)
    cur = await db.execute(
        "SELECT username, role, created_at FROM users WHERE id = ?", (uid,)
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="user not found")
    new_role = body.role if body.role is not None else row[1]

    # Refuse to demote the only admin.
    if row[1] == "admin" and new_role != "admin":
        cur = await db.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'")
        admin_count = (await cur.fetchone())[0]
        if admin_count <= 1:
            raise HTTPException(
                status_code=400, detail="cannot demote the only remaining admin"
            )

    if body.password is not None:
        # Bump token_version so a forced password reset also revokes the user's
        # existing sessions.
        await db.execute(
            "UPDATE users SET password_hash = ?, token_version = token_version + 1 WHERE id = ?",
            (hash_password(body.password), uid),
        )
    if body.role is not None and body.role != row[1]:
        await db.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, uid))
    await db.commit()
    if body.password is not None:
        await record(db, me, "user.password_reset", f"user={row[0]}")
    if body.role is not None and body.role != row[1]:
        await record(db, me, "user.role_change", f"user={row[0]} {row[1]} -> {new_role}")
    return UserListed(id=uid, username=row[0], role=new_role, created_at=row[2])


@router.delete("/{uid}", status_code=204)
async def delete_user(
    uid: int, request: Request, me: dict = Depends(current_user)
):
    if uid == me["id"]:
        raise HTTPException(status_code=400, detail="cannot delete your own account")
    db = _db(request)
    cur = await db.execute("SELECT role FROM users WHERE id = ?", (uid,))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="user not found")
    if row[0] == "admin":
        cur = await db.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'")
        if (await cur.fetchone())[0] <= 1:
            raise HTTPException(
                status_code=400, detail="cannot delete the only remaining admin"
            )
    await db.execute("DELETE FROM users WHERE id = ?", (uid,))
    await db.commit()
    await record(db, me, "user.delete", f"uid={uid}")
