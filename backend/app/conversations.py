import json
import time
import uuid
from typing import AsyncIterator

import aiosqlite
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .config import settings

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class ConversationSummary(BaseModel):
    id: str
    title: str
    model: str | None
    updated_at: int


class StoredMessage(BaseModel):
    id: int
    role: str
    content: str
    created_at: int


class Conversation(BaseModel):
    id: str
    title: str
    model: str | None
    created_at: int
    updated_at: int
    messages: list[StoredMessage]


class CreateBody(BaseModel):
    model: str | None = None


class SendBody(BaseModel):
    content: str
    model: str | None = None


def _db(request: Request) -> aiosqlite.Connection:
    return request.app.state.db


def _http(request: Request) -> httpx.AsyncClient:
    return request.app.state.http


@router.get("", response_model=list[ConversationSummary])
async def list_conversations(request: Request):
    db = _db(request)
    cur = await db.execute(
        """
        SELECT id, title, model, updated_at
        FROM conversations
        WHERE EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = conversations.id)
        ORDER BY updated_at DESC
        """
    )
    rows = await cur.fetchall()
    return [
        ConversationSummary(id=r[0], title=r[1], model=r[2], updated_at=r[3]) for r in rows
    ]


@router.post("", response_model=ConversationSummary)
async def create_conversation(body: CreateBody, request: Request):
    db = _db(request)
    cid = uuid.uuid4().hex
    now = int(time.time())
    await db.execute(
        "INSERT INTO conversations (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (cid, "new chat", body.model, now, now),
    )
    await db.commit()
    return ConversationSummary(id=cid, title="new chat", model=body.model, updated_at=now)


@router.get("/{cid}", response_model=Conversation)
async def get_conversation(cid: str, request: Request):
    db = _db(request)
    cur = await db.execute(
        "SELECT id, title, model, created_at, updated_at FROM conversations WHERE id = ?",
        (cid,),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="conversation not found")
    cur = await db.execute(
        "SELECT id, role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY id",
        (cid,),
    )
    msg_rows = await cur.fetchall()
    return Conversation(
        id=row[0],
        title=row[1],
        model=row[2],
        created_at=row[3],
        updated_at=row[4],
        messages=[
            StoredMessage(id=m[0], role=m[1], content=m[2], created_at=m[3]) for m in msg_rows
        ],
    )


@router.delete("/{cid}", status_code=204)
async def delete_conversation(cid: str, request: Request):
    db = _db(request)
    await db.execute("DELETE FROM conversations WHERE id = ?", (cid,))
    await db.commit()


@router.post("/{cid}/messages")
async def send_message(cid: str, body: SendBody, request: Request) -> StreamingResponse:
    db = _db(request)
    http = _http(request)

    cur = await db.execute(
        "SELECT title, model FROM conversations WHERE id = ?", (cid,)
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="conversation not found")
    title, conv_model = row

    cur = await db.execute(
        "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id", (cid,)
    )
    history_rows = await cur.fetchall()
    history = [{"role": r[0], "content": r[1]} for r in history_rows]

    now = int(time.time())
    await db.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (cid, "user", body.content, now),
    )
    if title == "new chat":
        new_title = body.content[:60].replace("\n", " ").strip() or "new chat"
        await db.execute("UPDATE conversations SET title = ? WHERE id = ?", (new_title, cid))
    chosen_model = body.model or conv_model or settings.default_model
    if body.model and body.model != conv_model:
        await db.execute(
            "UPDATE conversations SET model = ? WHERE id = ?", (body.model, cid)
        )
    await db.commit()

    upstream_messages = history + [{"role": "user", "content": body.content}]
    upstream_payload = {
        "model": chosen_model,
        "messages": upstream_messages,
        "stream": True,
    }

    async def proxy() -> AsyncIterator[bytes]:
        assembled: list[str] = []
        try:
            async with http.stream("POST", "/chat/completions", json=upstream_payload) as r:
                if r.status_code >= 400:
                    err = (await r.aread()).decode(errors="replace")
                    yield f"data: {json.dumps({'error': err})}\n\n".encode()
                    return
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("data: ") and not line.endswith("[DONE]"):
                        try:
                            chunk = json.loads(line[6:])
                            delta = (
                                chunk.get("choices", [{}])[0]
                                .get("delta", {})
                                .get("content")
                            )
                            if isinstance(delta, str) and delta:
                                assembled.append(delta)
                        except json.JSONDecodeError:
                            pass
                    yield f"{line}\n\n".encode()
        finally:
            final = "".join(assembled)
            if final:
                ts = int(time.time())
                await db.execute(
                    "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                    (cid, "assistant", final, ts),
                )
                await db.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?", (ts, cid)
                )
                await db.commit()

    return StreamingResponse(
        proxy(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
