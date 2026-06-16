"""Knowledge bases: named, reusable document collections that can be attached
to conversations (searched in RAG alongside the conversation's own uploads)."""
import time

import aiosqlite
import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from .auth import current_user
from .config import settings
from .rag import pack, prepare_document

router = APIRouter(prefix="/api", tags=["collections"], dependencies=[Depends(current_user)])


def _db(request: Request) -> aiosqlite.Connection:
    return request.app.state.db


def _http(request: Request) -> httpx.AsyncClient:
    return request.app.state.http


async def _owned_collection(db: aiosqlite.Connection, coll_id: int, user_id: int) -> None:
    cur = await db.execute(
        "SELECT 1 FROM collections WHERE id = ? AND user_id = ?", (coll_id, user_id)
    )
    if not await cur.fetchone():
        raise HTTPException(status_code=404, detail="collection not found")


async def _owned_conversation(db: aiosqlite.Connection, conv_id: str, user_id: int) -> None:
    cur = await db.execute(
        "SELECT 1 FROM conversations WHERE id = ? AND user_id = ?", (conv_id, user_id)
    )
    if not await cur.fetchone():
        raise HTTPException(status_code=404, detail="conversation not found")


class CollectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class CollectionOut(BaseModel):
    id: int
    name: str
    document_count: int
    created_at: int
    updated_at: int


class DocumentOut(BaseModel):
    id: int
    filename: str
    mime: str | None
    bytes: int
    chunk_count: int
    embedding_model: str | None
    created_at: int


# ---- collection CRUD ----

@router.get("/collections", response_model=list[CollectionOut])
async def list_collections(request: Request, user: dict = Depends(current_user)):
    db = _db(request)
    cur = await db.execute(
        """
        SELECT c.id, c.name, c.created_at, c.updated_at, COUNT(d.id)
        FROM collections c
        LEFT JOIN collection_documents d ON d.collection_id = c.id
        WHERE c.user_id = ?
        GROUP BY c.id, c.name, c.created_at, c.updated_at
        ORDER BY c.updated_at DESC
        """,
        (user["id"],),
    )
    return [
        CollectionOut(id=r[0], name=r[1], created_at=r[2], updated_at=r[3], document_count=r[4])
        for r in await cur.fetchall()
    ]


@router.post("/collections", response_model=CollectionOut)
async def create_collection(body: CollectionIn, request: Request, user: dict = Depends(current_user)):
    db = _db(request)
    now = int(time.time())
    coll_id = await db.insert(
        "INSERT INTO collections (user_id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (user["id"], body.name, now, now),
    )
    await db.commit()
    return CollectionOut(id=coll_id, name=body.name, document_count=0, created_at=now, updated_at=now)


@router.delete("/collections/{coll_id}", status_code=204)
async def delete_collection(coll_id: int, request: Request, user: dict = Depends(current_user)):
    db = _db(request)
    await _owned_collection(db, coll_id, user["id"])
    await db.execute("DELETE FROM collections WHERE id = ?", (coll_id,))
    await db.commit()


# ---- collection documents ----

@router.get("/collections/{coll_id}/documents", response_model=list[DocumentOut])
async def list_collection_documents(coll_id: int, request: Request, user: dict = Depends(current_user)):
    db = _db(request)
    await _owned_collection(db, coll_id, user["id"])
    cur = await db.execute(
        """
        SELECT id, filename, mime, bytes, chunk_count, embedding_model, created_at
        FROM collection_documents WHERE collection_id = ? ORDER BY id DESC
        """,
        (coll_id,),
    )
    return [
        DocumentOut(id=r[0], filename=r[1], mime=r[2], bytes=r[3], chunk_count=r[4], embedding_model=r[5], created_at=r[6])
        for r in await cur.fetchall()
    ]


@router.post("/collections/{coll_id}/documents", response_model=DocumentOut)
async def upload_collection_document(
    coll_id: int, request: Request, file: UploadFile = File(...), user: dict = Depends(current_user)
):
    db = _db(request)
    await _owned_collection(db, coll_id, user["id"])
    data = await file.read()
    if len(data) > settings.rag_max_upload_bytes:
        raise HTTPException(status_code=413, detail=f"file too large (max {settings.rag_max_upload_bytes} bytes)")

    chunks, embeddings = await prepare_document(_http(request), file.filename or "upload", file.content_type, data)
    now = int(time.time())
    doc_id = await db.insert(
        """
        INSERT INTO collection_documents
        (collection_id, filename, mime, bytes, chunk_count, embedding_model, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (coll_id, file.filename or "upload", file.content_type, len(data), len(chunks), settings.embedding_model, now),
    )
    await db.executemany(
        "INSERT INTO collection_chunks (document_id, seq, text, embedding) VALUES (?, ?, ?, ?)",
        [(doc_id, i, c, pack(v)) for i, (c, v) in enumerate(zip(chunks, embeddings))],
    )
    await db.execute("UPDATE collections SET updated_at = ? WHERE id = ?", (now, coll_id))
    await db.commit()
    return DocumentOut(
        id=doc_id, filename=file.filename or "upload", mime=file.content_type,
        bytes=len(data), chunk_count=len(chunks), embedding_model=settings.embedding_model, created_at=now,
    )


@router.delete("/collections/{coll_id}/documents/{doc_id}", status_code=204)
async def delete_collection_document(
    coll_id: int, doc_id: int, request: Request, user: dict = Depends(current_user)
):
    db = _db(request)
    await _owned_collection(db, coll_id, user["id"])
    await db.execute(
        "DELETE FROM collection_documents WHERE id = ? AND collection_id = ?", (doc_id, coll_id)
    )
    await db.commit()


# ---- attach collections to a conversation ----

class AttachIn(BaseModel):
    # Capped so a huge list can't overflow SQLite's bound-variable limit in the
    # ownership IN-clause (an unhandled 500); 200 is far beyond any real need.
    collection_ids: list[int] = Field(default_factory=list, max_length=200)


@router.get("/conversations/{conv_id}/collections")
async def get_conversation_collections(conv_id: str, request: Request, user: dict = Depends(current_user)) -> dict:
    db = _db(request)
    await _owned_conversation(db, conv_id, user["id"])
    cur = await db.execute(
        "SELECT collection_id FROM conversation_collections WHERE conversation_id = ?", (conv_id,)
    )
    return {"collection_ids": [r[0] for r in await cur.fetchall()]}


@router.put("/conversations/{conv_id}/collections")
async def set_conversation_collections(
    conv_id: str, body: AttachIn, request: Request, user: dict = Depends(current_user)
) -> dict:
    db = _db(request)
    await _owned_conversation(db, conv_id, user["id"])
    # only attach collections the user actually owns
    wanted = list(dict.fromkeys(body.collection_ids))
    owned: list[int] = []
    if wanted:
        placeholders = ",".join("?" * len(wanted))
        cur = await db.execute(
            f"SELECT id FROM collections WHERE user_id = ? AND id IN ({placeholders})",  # noqa: S608
            [user["id"], *wanted],
        )
        present = {r[0] for r in await cur.fetchall()}
        owned = [i for i in wanted if i in present]
    await db.execute("DELETE FROM conversation_collections WHERE conversation_id = ?", (conv_id,))
    for coll_id in owned:
        await db.execute(
            "INSERT INTO conversation_collections (conversation_id, collection_id) VALUES (?, ?)",
            (conv_id, coll_id),
        )
    await db.commit()
    return {"collection_ids": owned}
