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
from .objectstore import S3Store

router = APIRouter(prefix="/api/files", tags=["files"])

# Process-wide object store, configured once at startup (None -> DB storage,
# the zero-config default). A module singleton keeps the many file helpers from
# having to thread it through every call site.
_STORE: S3Store | None = None


def configure_store(store: S3Store | None) -> None:
    global _STORE
    _STORE = store

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


async def _load_bytes(file_id: str, data: Any, storage: Any) -> bytes | None:
    """Resolve a file row's bytes: the inline ``data`` blob for DB storage, or a
    fetch from the object store for ``storage == 's3'``. Returns None if the
    object is missing or the store is unreachable, so a single broken blob
    degrades gracefully (e.g. a bare ref on replay) instead of erroring."""
    if storage == "s3":
        if _STORE is None:
            return None
        try:
            return await _STORE.get(file_id)
        except Exception:
            return None
    return data


async def _delete_object(file_id: str, storage: Any) -> None:
    """Best-effort delete of an S3-backed object (no-op for DB storage). Failures
    are swallowed — a stranded object is a cost leak, not a correctness bug, and
    must never block the DB row deletion."""
    if storage == "s3" and _STORE is not None:
        try:
            await _STORE.delete(file_id)
        except Exception:
            pass


async def collect_conversation_objects(db: aiosqlite.Connection, conversation_id: str) -> list[str]:
    """Return the conversation's S3-backed file ids. Callers enumerate these
    BEFORE deleting the rows (the FK cascade clears the index but can't reach
    S3), then :func:`purge_objects` AFTER the DB delete commits. Empty when S3
    is off."""
    if _STORE is None:
        return []
    cur = await db.execute(
        "SELECT id FROM files WHERE conversation_id = ? AND storage = 's3'",
        (conversation_id,),
    )
    return [fid for (fid,) in await cur.fetchall()]


async def collect_user_objects(db: aiosqlite.Connection, user_id: int) -> list[str]:
    """Return every S3-backed file id reachable from a user (their own files,
    plus files in their conversations). Used to reclaim objects on user deletion,
    where the users->conversations->files cascade would otherwise strand them
    (files.user_id is SET NULL, conversation_id is CASCADE, so join on both)."""
    if _STORE is None:
        return []
    cur = await db.execute(
        "SELECT id FROM files WHERE storage = 's3' AND (user_id = ? OR conversation_id IN "
        "(SELECT id FROM conversations WHERE user_id = ?))",
        (user_id, user_id),
    )
    return [fid for (fid,) in await cur.fetchall()]


async def purge_objects(file_ids: list[str]) -> None:
    """Best-effort delete of S3 objects, called AFTER the owning DB rows are
    committed-deleted. Doing it post-commit (never before) means a rolled-back
    transaction can only ever strand an object (a cost leak) — it can never leave
    a live row pointing at an object we already deleted (a broken image)."""
    for fid in file_ids:
        await _delete_object(fid, "s3")


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
    if _STORE is not None:
        # Upload BEFORE inserting the index row, so a failed upload aborts the
        # whole persist (the caller's transaction never commits a dangling ref).
        await _STORE.put(fid, raw, mime)
        data, storage = b"", "s3"
    else:
        data, storage = raw, "db"
    await db.execute(
        "INSERT INTO files (id, user_id, conversation_id, mime, data, size, storage, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (fid, user_id, cid, mime, data, len(raw), storage, int(time.time())),
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
                    "SELECT mime, data, size, storage FROM files WHERE id = ? AND conversation_id = ?",
                    (m.group(1), conversation_id),
                )
                row = await cur.fetchone()
                if row is not None and (budget is None or row[2] <= budget[0]):
                    raw = await _load_bytes(m.group(1), row[1], row[3])
                    if raw is not None:
                        if budget is not None:
                            budget[0] -= row[2]
                        b64 = base64.b64encode(raw).decode("ascii")
                        part = {
                            **part,
                            "image_url": {**iu, "url": f"data:{row[0]};base64,{b64}"},
                        }
        out.append(part)
    return out


async def gc_orphan_files(db: aiosqlite.Connection, conversation_id: str) -> list[str]:
    """Delete this conversation's blob rows that no remaining message references,
    and return the stale S3 file ids for the caller to :func:`purge_objects`
    AFTER its commit (so a rollback can't strand a live row over a deleted
    object). Called after message truncation (edit/delete/regenerate) so
    superseded image blobs don't accumulate forever. Safe because every file
    row's ``conversation_id`` is its sole referencer (clones copy their own
    blobs)."""
    cur = await db.execute(
        "SELECT content FROM messages WHERE conversation_id = ?", (conversation_id,)
    )
    referenced: set[str] = set()
    for (content,) in await cur.fetchall():
        if isinstance(content, str) and "/api/files/" in content:
            referenced.update(_REF_FINDER.findall(content))
    cur = await db.execute(
        "SELECT id, storage FROM files WHERE conversation_id = ?", (conversation_id,)
    )
    stale = [(fid, storage) for (fid, storage) in await cur.fetchall() if fid not in referenced]
    stale_s3: list[str] = []
    for fid, storage in stale:
        await db.execute("DELETE FROM files WHERE id = ?", (fid,))
        if storage == "s3":
            stale_s3.append(fid)
    return stale_s3


async def clone_file_refs(
    db: aiosqlite.Connection, user_id: int, new_cid: str, source_cid: str, content: str
) -> str:
    """Copy every blob referenced in ``content`` into fresh rows owned by
    ``new_cid`` and rewrite the refs, so a cloned conversation is self-contained
    (deleting the original can't break the clone's images).

    Scoped to ``source_cid``: only blobs belonging to the conversation being
    cloned are copied. Without that scope a user could plant a forged
    ``/api/files/{someone-elses-id}`` ref in their own message (externalize_parts
    stores non-data-URL refs verbatim), clone it, and have the copy re-owned by
    them — exfiltrating another user's blob. A foreign ref simply isn't found, so
    it is left as a bare ref the clone's owner still can't read."""
    if not isinstance(content, str) or "/api/files/" not in content:
        return content
    for old_id in dict.fromkeys(_REF_FINDER.findall(content)):
        cur = await db.execute(
            "SELECT mime, data, size, storage FROM files WHERE id = ? AND conversation_id = ?",
            (old_id, source_cid),
        )
        row = await cur.fetchone()
        if row is None:
            continue
        mime, data, size, storage = row
        new_fid = _new_id()
        if storage == "s3":
            # Copy the object to a fresh key so the clone is self-contained
            # (deleting the original can't strand the clone's bytes).
            raw = await _load_bytes(old_id, data, storage)
            if raw is None:
                continue  # source object gone/unreachable; skip this ref
            await _STORE.put(new_fid, raw, mime)
            data = b""
        await db.execute(
            "INSERT INTO files (id, user_id, conversation_id, mime, data, size, storage, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (new_fid, user_id, new_cid, mime, data, size, storage, int(time.time())),
        )
        content = content.replace(f"/api/files/{old_id}", f"/api/files/{new_fid}")
    return content


@router.get("/{file_id}")
async def get_file(
    file_id: str, request: Request, user: dict = Depends(current_user)
) -> Response:
    db: aiosqlite.Connection = request.app.state.db
    cur = await db.execute(
        "SELECT f.mime, f.data, f.storage, f.user_id, c.user_id "
        "FROM files f LEFT JOIN conversations c ON c.id = f.conversation_id "
        "WHERE f.id = ?",
        (file_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="file not found")
    mime, data, storage, owner_id, conv_owner_id = row
    # Access control is decided BEFORE any object-store fetch, so an unauthorized
    # request never triggers an outbound S3 read.
    if user["role"] != "admin" and user["id"] not in (owner_id, conv_owner_id):
        raise HTTPException(status_code=404, detail="file not found")
    if storage == "s3":
        try:
            data = await _STORE.get(file_id) if _STORE is not None else None
        except Exception:
            raise HTTPException(status_code=502, detail="object store unreachable")
        if data is None:
            raise HTTPException(status_code=404, detail="file not found")
    return Response(
        content=data,
        media_type=mime or "application/octet-stream",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )
