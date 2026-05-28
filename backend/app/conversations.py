import json
import time
import uuid
from typing import Any, AsyncIterator

import aiosqlite
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .auth import current_user
from .config import settings

router = APIRouter(
    prefix="/api/conversations",
    tags=["conversations"],
    dependencies=[Depends(current_user)],
)


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
    system_prompt: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    stop: list[str] | None = None
    created_at: int
    updated_at: int
    messages: list[StoredMessage]


class CreateBody(BaseModel):
    model: str | None = None


class SendBody(BaseModel):
    # Plain text, or an OpenAI multimodal content array
    # ([{type: "text", text: "..."}, {type: "image_url", image_url: {"url": "data:..."}}]).
    content: str | list[dict]
    model: str | None = None


class RegenerateBody(BaseModel):
    model: str | None = None


class EditBody(BaseModel):
    content: str | list[dict]
    model: str | None = None


class UpdateBody(BaseModel):
    title: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    stop: list[str] | None = None


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


def _decode_content(raw: str) -> str | list[dict]:
    """Multimodal messages are stored as JSON-encoded part arrays.

    Heuristic-then-parse: if the raw value looks like a JSON array, try to
    parse it. Anything else is plain text.
    """
    if raw.startswith("[") and raw.endswith("]"):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
    return raw


def _encode_content(content: str | list[dict]) -> str:
    if isinstance(content, list):
        return json.dumps(content)
    return content


def _content_preview(content: str | list[dict]) -> str:
    """Short summary of a message (used for auto-title)."""
    if isinstance(content, list):
        for part in content:
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                return part["text"]
        return "image"
    return content


async def _load_history(db: aiosqlite.Connection, cid: str) -> list[dict]:
    cur = await db.execute(
        "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id", (cid,)
    )
    rows = await cur.fetchall()
    return [{"role": r[0], "content": _decode_content(r[1])} for r in rows]


async def _conv_settings(db: aiosqlite.Connection, cid: str) -> dict[str, Any]:
    cur = await db.execute(
        """
        SELECT title, model, system_prompt, temperature, top_p, stop
        FROM conversations WHERE id = ?
        """,
        (cid,),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="conversation not found")
    stop_raw = row[5]
    return {
        "title": row[0],
        "model": row[1],
        "system_prompt": row[2],
        "temperature": row[3],
        "top_p": row[4],
        "stop": json.loads(stop_raw) if stop_raw else None,
    }


def _build_upstream_messages(
    system_prompt: str | None, history: list[dict], extra: list[dict] | None = None
) -> list[dict]:
    msgs: list[dict] = []
    if system_prompt:
        msgs.append({"role": "system", "content": system_prompt})
    msgs.extend(history)
    if extra:
        msgs.extend(extra)
    return msgs


async def _stream_and_persist(
    db: aiosqlite.Connection,
    http: httpx.AsyncClient,
    cid: str,
    upstream_messages: list[dict],
    model: str,
    temperature: float | None,
    top_p: float | None,
    stop: list[str] | None,
) -> AsyncIterator[bytes]:
    assembled: list[str] = []
    payload: dict[str, Any] = {
        "model": model,
        "messages": upstream_messages,
        "stream": True,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if top_p is not None:
        payload["top_p"] = top_p
    if stop:
        payload["stop"] = stop

    try:
        async with http.stream("POST", "/chat/completions", json=payload) as r:
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
                            chunk.get("choices", [{}])[0].get("delta", {}).get("content")
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


@router.get("", response_model=list[ConversationSummary])
async def list_conversations(
    request: Request, user: dict = Depends(current_user)
):
    db = _db(request)
    cur = await db.execute(
        """
        SELECT id, title, model, updated_at
        FROM conversations
        WHERE user_id = ?
          AND EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = conversations.id)
        ORDER BY updated_at DESC
        """,
        (user["id"],),
    )
    rows = await cur.fetchall()
    return [
        ConversationSummary(id=r[0], title=r[1], model=r[2], updated_at=r[3]) for r in rows
    ]


@router.post("", response_model=ConversationSummary)
async def create_conversation(
    body: CreateBody, request: Request, user: dict = Depends(current_user)
):
    db = _db(request)
    cid = uuid.uuid4().hex
    now = int(time.time())
    await db.execute(
        "INSERT INTO conversations (id, user_id, title, model, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (cid, user["id"], "new chat", body.model, now, now),
    )
    await db.commit()
    return ConversationSummary(id=cid, title="new chat", model=body.model, updated_at=now)


@router.get("/{cid}", response_model=Conversation)
async def get_conversation(
    cid: str, request: Request, user: dict = Depends(current_user)
):
    db = _db(request)
    await _owned(db, cid, user["id"])
    cur = await db.execute(
        """
        SELECT id, title, model, system_prompt, temperature, top_p, stop, created_at, updated_at
        FROM conversations WHERE id = ?
        """,
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
        system_prompt=row[3],
        temperature=row[4],
        top_p=row[5],
        stop=json.loads(row[6]) if row[6] else None,
        created_at=row[7],
        updated_at=row[8],
        messages=[
            StoredMessage(id=m[0], role=m[1], content=m[2], created_at=m[3]) for m in msg_rows
        ],
    )


@router.patch("/{cid}", response_model=Conversation)
async def update_conversation(
    cid: str, body: UpdateBody, request: Request, user: dict = Depends(current_user)
):
    db = _db(request)
    await _owned(db, cid, user["id"])
    fields = body.model_dump(exclude_unset=True)
    if fields:
        sets: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            sets.append(f"{key} = ?")
            if key == "stop":
                params.append(json.dumps(value) if value else None)
            else:
                params.append(value)
        params.append(cid)
        await db.execute(
            f"UPDATE conversations SET {', '.join(sets)} WHERE id = ?", params
        )
        await db.commit()
    return await get_conversation(cid, request, user)


@router.delete("/{cid}", status_code=204)
async def delete_conversation(
    cid: str, request: Request, user: dict = Depends(current_user)
):
    db = _db(request)
    await _owned(db, cid, user["id"])
    await db.execute("DELETE FROM conversations WHERE id = ?", (cid,))
    await db.commit()


async def _maybe_update_title(
    db: aiosqlite.Connection, cid: str, title: str, first_user: str
) -> None:
    if title == "new chat":
        new_title = first_user[:60].replace("\n", " ").strip() or "new chat"
        await db.execute(
            "UPDATE conversations SET title = ? WHERE id = ?", (new_title, cid)
        )


async def _maybe_update_model(
    db: aiosqlite.Connection, cid: str, requested: str | None, current: str | None
) -> str:
    chosen = requested or current or settings.default_model
    if requested and requested != current:
        await db.execute(
            "UPDATE conversations SET model = ? WHERE id = ?", (requested, cid)
        )
    return chosen


@router.post("/{cid}/messages")
async def send_message(
    cid: str, body: SendBody, request: Request, user: dict = Depends(current_user)
) -> StreamingResponse:
    db = _db(request)
    await _owned(db, cid, user["id"])
    http = _http(request)

    conv = await _conv_settings(db, cid)
    history = await _load_history(db, cid)

    now = int(time.time())
    await db.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (cid, "user", _encode_content(body.content), now),
    )
    await _maybe_update_title(db, cid, conv["title"], _content_preview(body.content))
    chosen_model = await _maybe_update_model(db, cid, body.model, conv["model"])
    await db.commit()

    upstream = _build_upstream_messages(
        conv["system_prompt"], history, [{"role": "user", "content": body.content}]
    )
    return StreamingResponse(
        _stream_and_persist(
            db, http, cid, upstream, chosen_model,
            conv["temperature"], conv["top_p"], conv["stop"],
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{cid}/regenerate")
async def regenerate(
    cid: str, body: RegenerateBody, request: Request, user: dict = Depends(current_user)
) -> StreamingResponse:
    db = _db(request)
    await _owned(db, cid, user["id"])
    http = _http(request)

    conv = await _conv_settings(db, cid)

    cur = await db.execute(
        "SELECT id, role FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT 1",
        (cid,),
    )
    last = await cur.fetchone()
    if not last or last[1] != "assistant":
        raise HTTPException(
            status_code=400, detail="nothing to regenerate (no trailing assistant message)"
        )
    await db.execute("DELETE FROM messages WHERE id = ?", (last[0],))
    chosen_model = await _maybe_update_model(db, cid, body.model, conv["model"])
    await db.commit()

    history = await _load_history(db, cid)
    upstream = _build_upstream_messages(conv["system_prompt"], history)
    return StreamingResponse(
        _stream_and_persist(
            db, http, cid, upstream, chosen_model,
            conv["temperature"], conv["top_p"], conv["stop"],
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.patch("/{cid}/messages/{msg_id}")
async def edit_message(
    cid: str,
    msg_id: int,
    body: EditBody,
    request: Request,
    user: dict = Depends(current_user),
) -> StreamingResponse:
    db = _db(request)
    await _owned(db, cid, user["id"])
    http = _http(request)

    cur = await db.execute(
        "SELECT role FROM messages WHERE id = ? AND conversation_id = ?", (msg_id, cid)
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="message not found")
    if row[0] != "user":
        raise HTTPException(status_code=400, detail="only user messages can be edited")

    conv = await _conv_settings(db, cid)

    await db.execute(
        "UPDATE messages SET content = ? WHERE id = ?", (_encode_content(body.content), msg_id)
    )
    await db.execute(
        "DELETE FROM messages WHERE conversation_id = ? AND id > ?", (cid, msg_id)
    )
    await _maybe_update_title(db, cid, conv["title"], _content_preview(body.content))
    chosen_model = await _maybe_update_model(db, cid, body.model, conv["model"])
    await db.commit()

    history = await _load_history(db, cid)
    upstream = _build_upstream_messages(conv["system_prompt"], history)
    return StreamingResponse(
        _stream_and_persist(
            db, http, cid, upstream, chosen_model,
            conv["temperature"], conv["top_p"], conv["stop"],
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
