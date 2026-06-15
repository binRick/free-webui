"""Object/media store.

Large base64 image payloads (generated images from `imagine`/`run_python`,
user-uploaded vision attachments) are extracted out of message content and
stored as binary blobs in the ``files`` table, served on demand via
``GET /api/files/{id}``. Message rows — and therefore every conversation
fetch — stay small instead of carrying multi-megabyte ``data:`` URLs inline.

``externalize_parts`` runs at persist time (data URL -> ``/api/files/{id}``);
``expand_file_refs`` runs on the way upstream (so vision replay still sees the
real bytes) and for public share rendering (the unauthenticated viewer can't
hit the auth-gated file endpoint, so images are inlined back into the payload).
"""

from __future__ import annotations

import base64
import binascii
import re
import secrets
import time
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .auth import current_user

router = APIRouter(prefix="/api/files", tags=["files"])

# data:[<mediatype>][;base64],<payload>
_DATA_URL_RE = re.compile(r"^data:([\w.+-]+/[\w.+-]+)?(;base64)?,(.*)$", re.DOTALL)
_REF_RE = re.compile(r"^/api/files/([A-Za-z0-9_-]+)$")
# Finds every /api/files/{id} ref embedded anywhere in a (JSON-encoded) message.
_REF_FINDER = re.compile(r"/api/files/([A-Za-z0-9_-]+)")
_MAX_DECODED = 25 * 1024 * 1024  # 25 MB per object guard
# base64 inflates by 4/3; reject the encoded string before decoding so an
# oversized data: URL can't force a large decode allocation.
_MAX_ENCODED = (_MAX_DECODED // 3 + 1) * 4
# Aggregate cap on bytes inlined back into a single replay/share payload, so a
# conversation with many large images can't blow up per-request memory.
_INLINE_BUDGET = 16 * 1024 * 1024


def _new_id() -> str:
    return secrets.token_urlsafe(16)


async def store_data_url(
    db: aiosqlite.Connection, user_id: int, cid: str | None, url: Any
) -> Any:
    """If ``url`` is a base64 ``data:`` URL, persist its bytes and return the
    ``/api/files/{id}`` ref. Anything else (http URL, an existing ref, plain
    text, oversized/undecodable payload) is returned unchanged."""
    if not isinstance(url, str):
        return url
    m = _DATA_URL_RE.match(url)
    if not m or not m.group(2):  # only base64 data URLs are externalized
        return url
    mime = m.group(1) or "application/octet-stream"
    if len(m.group(3)) > _MAX_ENCODED:  # too big to decode within the cap
        return url
    try:
        raw = base64.b64decode(m.group(3), validate=True)
    except (binascii.Error, ValueError):
        return url
    if not raw or len(raw) > _MAX_DECODED:
        return url
    fid = _new_id()
    await db.execute(
        "INSERT INTO files (id, user_id, conversation_id, mime, data, size, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (fid, user_id, cid, mime, raw, len(raw), int(time.time())),
    )
    return f"/api/files/{fid}"


async def externalize_parts(
    db: aiosqlite.Connection, user_id: int, cid: str | None, content: Any
) -> Any:
    """Replace base64 ``data:`` image URLs in a multimodal content array with
    ``/api/files/{id}`` refs. Plain-string content passes through untouched."""
    if not isinstance(content, list):
        return content
    out = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "image_url":
            iu = part.get("image_url")
            if isinstance(iu, dict) and isinstance(iu.get("url"), str):
                ref = await store_data_url(db, user_id, cid, iu["url"])
                if ref != iu["url"]:
                    part = {**part, "image_url": {**iu, "url": ref}}
        out.append(part)
    return out


async def expand_file_refs(
    db: aiosqlite.Connection,
    content: Any,
    conversation_id: str | None,
    budget: list[int] | None = None,
) -> Any:
    """Inverse of :func:`externalize_parts`: turn ``/api/files/{id}`` image
    refs back into inline base64 ``data:`` URLs by loading the stored bytes.

    Scoped to ``conversation_id``: a ref is only inlined when the file belongs
    to this conversation. Without that scope a user could embed a forged
    ``/api/files/{someone-elses-id}`` ref in their own message and exfiltrate
    another user's blob through the upstream replay or the public share path.

    ``budget`` is an optional single-element ``[remaining_bytes]`` cell, shared
    across the messages of one replay/share, that bounds the total bytes inlined
    into that payload. Once exhausted, further refs are left as bare refs rather
    than accumulating unbounded memory.
    """
    if not isinstance(content, list):
        return content
    out = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "image_url":
            iu = part.get("image_url")
            url = iu.get("url") if isinstance(iu, dict) else None
            m = _REF_RE.match(url) if isinstance(url, str) else None
            if m and (budget is None or budget[0] > 0):
                cur = await db.execute(
                    "SELECT mime, data, size FROM files WHERE id = ? AND conversation_id = ?",
                    (m.group(1), conversation_id),
                )
                row = await cur.fetchone()
                if row is not None and (budget is None or row[2] <= budget[0]):
                    if budget is not None:
                        budget[0] -= row[2]
                    b64 = base64.b64encode(row[1]).decode("ascii")
                    part = {
                        **part,
                        "image_url": {**iu, "url": f"data:{row[0]};base64,{b64}"},
                    }
        out.append(part)
    return out


async def gc_orphan_files(db: aiosqlite.Connection, conversation_id: str) -> None:
    """Delete this conversation's blob rows that no remaining message references.

    Called after message truncation (edit/delete/regenerate) so superseded image
    blobs don't accumulate forever. Safe because every file row's
    ``conversation_id`` is its sole referencer (clones copy their own blobs)."""
    cur = await db.execute(
        "SELECT content FROM messages WHERE conversation_id = ?", (conversation_id,)
    )
    referenced: set[str] = set()
    for (content,) in await cur.fetchall():
        if isinstance(content, str) and "/api/files/" in content:
            referenced.update(_REF_FINDER.findall(content))
    cur = await db.execute(
        "SELECT id FROM files WHERE conversation_id = ?", (conversation_id,)
    )
    stale = [fid for (fid,) in await cur.fetchall() if fid not in referenced]
    for fid in stale:
        await db.execute("DELETE FROM files WHERE id = ?", (fid,))


async def clone_file_refs(
    db: aiosqlite.Connection, user_id: int, new_cid: str, content: str
) -> str:
    """Copy every blob referenced in ``content`` into fresh rows owned by
    ``new_cid`` and rewrite the refs, so a cloned conversation is self-contained
    (deleting the original can't break the clone's images)."""
    if not isinstance(content, str) or "/api/files/" not in content:
        return content
    for old_id in dict.fromkeys(_REF_FINDER.findall(content)):
        cur = await db.execute(
            "SELECT mime, data, size FROM files WHERE id = ?", (old_id,)
        )
        row = await cur.fetchone()
        if row is None:
            continue
        new_fid = _new_id()
        await db.execute(
            "INSERT INTO files (id, user_id, conversation_id, mime, data, size, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (new_fid, user_id, new_cid, row[0], row[1], row[2], int(time.time())),
        )
        content = content.replace(f"/api/files/{old_id}", f"/api/files/{new_fid}")
    return content


@router.get("/{file_id}")
async def get_file(
    file_id: str, request: Request, user: dict = Depends(current_user)
) -> Response:
    db: aiosqlite.Connection = request.app.state.db
    cur = await db.execute(
        "SELECT f.mime, f.data, f.user_id, c.user_id "
        "FROM files f LEFT JOIN conversations c ON c.id = f.conversation_id "
        "WHERE f.id = ?",
        (file_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="file not found")
    mime, data, owner_id, conv_owner_id = row
    if user["role"] != "admin" and user["id"] not in (owner_id, conv_owner_id):
        raise HTTPException(status_code=404, detail="file not found")
    return Response(
        content=data,
        media_type=mime or "application/octet-stream",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )
