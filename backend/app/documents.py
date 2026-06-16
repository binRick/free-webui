import time

import aiosqlite
import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from .auth import current_user
from .config import settings
from .rag import pack, prepare_document

router = APIRouter(
    prefix="/api/conversations",
    tags=["documents"],
    dependencies=[Depends(current_user)],
)


class DocumentOut(BaseModel):
    id: int
    filename: str
    mime: str | None
    bytes: int
    chunk_count: int
    embedding_model: str | None
    created_at: int


def _db(request: Request) -> aiosqlite.Connection:
    return request.app.state.db


def _http(request: Request) -> httpx.AsyncClient:
    return request.app.state.http


async def _owned(db: aiosqlite.Connection, cid: str, user_id: int) -> None:
    cur = await db.execute(
        "SELECT 1 FROM conversations WHERE id = ? AND user_id = ?", (cid, user_id)
    )
    if not await cur.fetchone():
        raise HTTPException(status_code=404, detail="conversation not found")


@router.get("/{cid}/documents", response_model=list[DocumentOut])
async def list_documents(
    cid: str, request: Request, user: dict = Depends(current_user)
):
    db = _db(request)
    await _owned(db, cid, user["id"])
    cur = await db.execute(
        """
        SELECT id, filename, mime, bytes, chunk_count, embedding_model, created_at
        FROM documents WHERE conversation_id = ? ORDER BY id DESC
        """,
        (cid,),
    )
    return [
        DocumentOut(
            id=r[0], filename=r[1], mime=r[2], bytes=r[3],
            chunk_count=r[4], embedding_model=r[5], created_at=r[6],
        )
        for r in await cur.fetchall()
    ]


@router.post("/{cid}/documents", response_model=DocumentOut)
async def upload_document(
    cid: str,
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(current_user),
):
    db = _db(request)
    await _owned(db, cid, user["id"])

    data = await file.read()
    if len(data) > settings.rag_max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"file too large (max {settings.rag_max_upload_bytes} bytes)",
        )

    http = _http(request)
    chunks, embeddings = await prepare_document(http, file.filename or "upload", file.content_type, data)

    now = int(time.time())
    doc_id = await db.insert(
        """
        INSERT INTO documents
        (conversation_id, filename, mime, bytes, chunk_count, embedding_model, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            cid,
            file.filename or "upload",
            file.content_type,
            len(data),
            len(chunks),
            settings.embedding_model,
            now,
        ),
    )

    await db.executemany(
        "INSERT INTO chunks (document_id, seq, text, embedding) VALUES (?, ?, ?, ?)",
        [(doc_id, i, c, pack(v)) for i, (c, v) in enumerate(zip(chunks, embeddings))],
    )
    await db.commit()

    return DocumentOut(
        id=doc_id,
        filename=file.filename or "upload",
        mime=file.content_type,
        bytes=len(data),
        chunk_count=len(chunks),
        embedding_model=settings.embedding_model,
        created_at=now,
    )


@router.delete("/{cid}/documents/{doc_id}", status_code=204)
async def delete_document(
    cid: str,
    doc_id: int,
    request: Request,
    user: dict = Depends(current_user),
):
    db = _db(request)
    await _owned(db, cid, user["id"])
    await db.execute(
        "DELETE FROM documents WHERE id = ? AND conversation_id = ?", (doc_id, cid)
    )
    await db.commit()
