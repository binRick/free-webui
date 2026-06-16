import json
import time
import uuid
from typing import Any, AsyncIterator

import datetime
import re

import aiosqlite
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from .access import can_access_model
from .auth import current_user
from .config import settings
from .connections import Connection, conn_headers, conn_url, config_connection, resolve_connection
from .files import (
    _INLINE_BUDGET,
    clone_file_refs,
    expand_file_refs,
    externalize_parts,
    gc_orphan_files,
    store_data_url,
)
from .mcp import compose_tool_specs, run_tool as run_dispatch
from .memories import load_memory_context
from .plugins import PluginContext, PluginRegistry, run_inlet, run_outlet
from .tools import ToolContext
from .rag import retrieve_context
from .web_search import format_context as format_web_context
from .web_search import search as web_search

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
    pinned: bool = False
    archived: bool = False
    folder_id: int | None = None
    tags: list[str] = []


class StoredMessage(BaseModel):
    id: int
    role: str
    content: str
    created_at: int
    rating: int | None = None  # current user's feedback: 1 (up), -1 (down), or None
    sources: list[dict] | None = None  # RAG/web sources that grounded this reply


class Conversation(BaseModel):
    id: str
    title: str
    model: str | None
    system_prompt: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    stop: list[str] | None = None
    web_search: bool = False
    tools_enabled: bool = False
    max_tokens: int | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    seed: int | None = None
    folder_id: int | None = None
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
    web_search: bool | None = None
    tools_enabled: bool | None = None
    max_tokens: int | None = Field(default=None, ge=1, le=131072)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    seed: int | None = Field(default=None, ge=0)
    pinned: bool | None = None
    archived: bool | None = None
    # 3-state via model_fields_set: omitted = leave as-is, an int = move into that
    # folder (validated to one the caller owns), explicit null = remove from folder.
    folder_id: int | None = Field(default=None)


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


def _content_text(content: str | list[dict]) -> str:
    """Plain-text view of a message, used for RAG query embedding."""
    if isinstance(content, list):
        return " ".join(
            p.get("text", "") for p in content if p.get("type") == "text"
        ).strip()
    return content


def _history_content_for_upstream(role: str, content: str | list[dict]):
    """Generated images persist inside assistant messages as image_url parts,
    but most chat-completion providers reject image parts in assistant turns
    (and replaying the large data: URL bloats context). Downcast assistant
    multimodal content to its text on the way upstream; user messages keep
    their images (vision input). The stored/displayed message is untouched."""
    if role == "assistant" and isinstance(content, list):
        return _content_text(content) or "[generated an image]"
    return content


async def _load_history(db: aiosqlite.Connection, cid: str) -> list[dict]:
    cur = await db.execute(
        "SELECT role, content FROM messages WHERE conversation_id = ? AND active = 1 ORDER BY id",
        (cid,),
    )
    rows = await cur.fetchall()
    history = []
    budget = [_INLINE_BUDGET]  # bounds total image bytes inlined into one replay
    for r in rows:
        content = _history_content_for_upstream(r[0], _decode_content(r[1]))
        # User vision attachments persist as /api/files/{id} refs; inline the
        # real bytes so the upstream model actually sees the image on replay.
        if isinstance(content, list):
            content = await expand_file_refs(db, content, cid, budget)
        history.append({"role": r[0], "content": content})
    return history


async def _conv_settings(db: aiosqlite.Connection, cid: str) -> dict[str, Any]:
    cur = await db.execute(
        """
        SELECT title, model, system_prompt, temperature, top_p, stop,
               web_search, tools_enabled, max_tokens, presence_penalty,
               frequency_penalty, seed
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
        "web_search": bool(row[6]),
        "tools_enabled": bool(row[7]),
        "max_tokens": row[8],
        "presence_penalty": row[9],
        "frequency_penalty": row[10],
        "seed": row[11],
    }


async def _gather_context(
    db: aiosqlite.Connection,
    http: httpx.AsyncClient,
    cid: str,
    query: str,
    conv: dict,
    user_id: int,
) -> tuple[str | None, list[dict]]:
    """Build the injected system context (memories + web + RAG) and the list of
    sources (documents + web results) that grounded it."""
    rag_ctx, sources = await retrieve_context(db, http, cid, query)
    web_ctx = None
    if conv.get("web_search"):
        results = await web_search(query)
        web_ctx = format_web_context(results)
        for r in results:
            sources.append({
                "kind": "web",
                "label": r.get("title") or r.get("url") or "result",
                "detail": r.get("url", ""),
            })
    mem_ctx = await load_memory_context(db, user_id)
    context = "\n\n".join(c for c in (mem_ctx, web_ctx, rag_ctx) if c) or None
    return context, sources


def _build_upstream_messages(
    system_prompt: str | None,
    context: str | None,
    history: list[dict],
    extra: list[dict] | None = None,
) -> list[dict]:
    msgs: list[dict] = []
    if system_prompt:
        msgs.append({"role": "system", "content": system_prompt})
    if context:
        msgs.append({"role": "system", "content": context})
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
    tools_enabled: bool = False,
    user_id: int | None = None,
    max_tool_loops: int = 5,
    plugins: PluginRegistry | None = None,
    parent_message_id: int | None = None,
    conn: Connection | None = None,
    gen: dict[str, Any] | None = None,
    sources: list[dict] | None = None,
    continue_message_id: int | None = None,
    persist: bool = True,
) -> AsyncIterator[bytes]:
    """Stream a chat completion. If tools_enabled, runs a tool loop until
    the upstream finishes with a non-tool finish reason, surfacing each
    tool call to the client as an `event: tool_call` SSE frame.
    """
    msgs = list(upstream_messages)
    final_content: list[str] = []
    tools_executed: list[dict] = []
    generated_images: list[str] = []
    tool_ctx = ToolContext()
    conn = conn or config_connection()

    # Compose the tool catalogue once at the start of the turn (built-in
    # + every enabled MCP server's tools/list); reused across tool loops.
    tool_specs: list[dict] = []
    dispatch: dict[str, tuple[int, str]] = {}
    if tools_enabled and user_id is not None:
        tool_specs, dispatch = await compose_tool_specs(db, user_id)

    # Assemble the request body ONCE so a plugin inlet's edits (model, params,
    # messages) survive across tool-loop iterations. Tools/tool_choice are owned
    # by the loop, not by plugins, so they're applied per-iteration below.
    body: dict[str, Any] = {"model": model, "messages": msgs}
    if temperature is not None:
        body["temperature"] = temperature
    if top_p is not None:
        body["top_p"] = top_p
    if stop:
        body["stop"] = stop
    # Optional OpenAI-standard generation params, forwarded only when set.
    for key in ("max_tokens", "presence_penalty", "frequency_penalty", "seed"):
        val = (gen or {}).get(key)
        if val is not None:
            body[key] = val

    if plugins:
        ctx = PluginContext(
            db=db, http=http, user_id=user_id, conversation_id=cid, model=body["model"]
        )
        body = await run_inlet(plugins, body, ctx)
        body.pop("tools", None)  # the tool loop owns the spec/dispatch coupling
        body.pop("tool_choice", None)
        # Re-assert the required keys in case an inlet returned a partial dict:
        # model must survive (a dropped model would 400 the upstream), and msgs
        # is rebound from the body only when the inlet supplied a real list.
        body.setdefault("model", model)
        new_msgs = body.get("messages")
        if isinstance(new_msgs, list):
            msgs = new_msgs

    def _payload() -> dict[str, Any]:
        p: dict[str, Any] = {**body, "messages": msgs, "stream": True}
        if tools_enabled and tool_specs:
            p["tools"] = tool_specs
            p["tool_choice"] = "auto"
        return p

    sources_sent = False
    try:
        for _ in range(max_tool_loops):
            iter_content: list[str] = []
            tool_buf: dict[int, dict] = {}
            saw_done = False

            async with http.stream(
                "POST", conn_url(conn, "chat/completions"),
                json=_payload(), headers=conn_headers(conn),
            ) as r:
                if r.status_code >= 400:
                    err = (await r.aread()).decode(errors="replace")
                    # OpenAI-shaped error object (matches the /v1 proxy); the
                    # finally block emits the single trailing [DONE].
                    frame = {
                        "error": {
                            "message": f"upstream error {r.status_code}: {err[:300]}",
                            "type": "upstream_error",
                        }
                    }
                    yield f"data: {json.dumps(frame)}\n\n".encode()
                    return
                # Emit grounding sources only once the upstream actually started
                # streaming, so an upstream error doesn't leave a phantom strip.
                if sources and not sources_sent:
                    yield f"event: sources\ndata: {json.dumps(sources)}\n\n".encode()
                    sources_sent = True

                async for line in r.aiter_lines():
                    if not line:
                        continue
                    if line.endswith("[DONE]"):
                        saw_done = True
                        # only emit [DONE] when the OUTER loop finishes
                        continue
                    if not line.startswith("data: "):
                        continue
                    try:
                        chunk = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue

                    delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        iter_content.append(content)
                        # forward content chunk to client
                        yield f"{line}\n\n".encode()

                    tc = delta.get("tool_calls")
                    if isinstance(tc, list):
                        for c in tc:
                            idx = int(c.get("index", 0))
                            rec = tool_buf.setdefault(
                                idx, {"id": "", "name": "", "args": ""}
                            )
                            if c.get("id"):
                                rec["id"] = c["id"]
                            fn = c.get("function") or {}
                            if fn.get("name"):
                                rec["name"] = fn["name"]
                            if fn.get("arguments") is not None:
                                rec["args"] += fn["arguments"]

            final_content.extend(iter_content)

            if not tool_buf:
                # natural end of generation
                _ = saw_done
                break

            # Replay the assistant tool_calls message + each tool's result
            # back to the upstream, and surface each tool call to the client.
            assistant_tool_msg: dict[str, Any] = {
                "role": "assistant",
                "content": "".join(iter_content),
                "tool_calls": [
                    {
                        "id": rec["id"],
                        "type": "function",
                        "function": {
                            "name": rec["name"],
                            "arguments": rec["args"],
                        },
                    }
                    for _i, rec in sorted(tool_buf.items())
                ],
            }
            msgs.append(assistant_tool_msg)

            for _i, rec in sorted(tool_buf.items()):
                try:
                    args = json.loads(rec["args"]) if rec["args"] else {}
                except json.JSONDecodeError:
                    args = {}
                images_before = len(tool_ctx.images)
                result = await run_dispatch(
                    db, user_id or 0, dispatch, rec["name"], args, tool_ctx
                )
                tools_executed.append(
                    {"name": rec["name"], "arguments": args, "result": result}
                )
                event = json.dumps(
                    {"name": rec["name"], "arguments": args, "result": result}
                )
                yield f"event: tool_call\ndata: {event}\n\n".encode()
                # Surface any images this tool produced (e.g. `imagine`) so the
                # live UI can render them; also collect them for persistence.
                for data_url in tool_ctx.images[images_before:]:
                    generated_images.append(data_url)
                    img_event = json.dumps({"url": data_url})
                    yield f"event: image\ndata: {img_event}\n\n".encode()
                msgs.append(
                    {
                        "role": "tool",
                        "tool_call_id": rec["id"],
                        "content": result,
                    }
                )
    finally:
        yield b"data: [DONE]\n\n"
        text = "".join(final_content)
        # Only run the outlet / persist when the turn actually produced output.
        # An upstream error or empty completion must not let an outlet fabricate
        # a phantom assistant message out of empty text (e.g. a footer/banner
        # outlet turning "" into non-empty), and the outlet should observe only
        # real model output. `persist=False` (temporary chat) streams without any
        # DB writes at all.
        if persist and (text or generated_images):
            if plugins:
                octx = PluginContext(
                    db=db, http=http, user_id=user_id, conversation_id=cid,
                    model=body.get("model", model),
                )
                text = await run_outlet(plugins, text, octx)
            if text or generated_images:
                ts = int(time.time())
                if continue_message_id is not None:
                    # "Continue generation": append the new text onto the
                    # existing (text-only) assistant message rather than starting
                    # a fresh one. tools are off for continue, so no images here.
                    await db.execute(
                        "UPDATE messages SET content = content || ? WHERE id = ?",
                        (text, continue_message_id),
                    )
                else:
                    if generated_images:
                        # Persist as a multimodal message so the generated
                        # image(s) survive a reload, rendered by the same
                        # image-part path as user-uploaded images.
                        parts: list[dict] = []
                        if text:
                            parts.append({"type": "text", "text": text})
                        for url in generated_images:
                            ref = await store_data_url(db, user_id or 0, cid, url)
                            parts.append({"type": "image_url", "image_url": {"url": ref}})
                        stored = _encode_content(parts)
                    else:
                        stored = text
                    await db.execute(
                        "INSERT INTO messages "
                        "(conversation_id, role, content, parent_id, sources, model, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (cid, "assistant", stored, parent_message_id,
                         json.dumps(sources) if sources else None,
                         body.get("model", model), ts),
                    )
                await db.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?", (ts, cid)
                )
                await db.commit()


@router.get("", response_model=list[ConversationSummary])
async def list_conversations(
    request: Request,
    user: dict = Depends(current_user),
    q: str | None = Query(default=None, max_length=200),
    archived: bool = Query(default=False),
    tag: str | None = Query(default=None, max_length=80),
    folder_id: int | None = Query(default=None),
):
    db = _db(request)
    params: list[Any] = [user["id"]]
    search_sql = f"AND conversations.archived = {1 if archived else 0}"
    if tag:
        search_sql += (
            " AND EXISTS (SELECT 1 FROM conversation_tags t "
            "WHERE t.conversation_id = conversations.id AND t.tag = ?)"
        )
        params.append(tag)
    if folder_id is not None:
        search_sql += " AND conversations.folder_id = ?"
        params.append(folder_id)
    term = (q or "").strip()
    if term:
        # Match the title OR any message body (active or not) in the conversation.
        like = f"%{term}%"
        search_sql += """
          AND (
            conversations.title LIKE ? COLLATE NOCASE
            OR EXISTS (
              SELECT 1 FROM messages m2
              WHERE m2.conversation_id = conversations.id
                AND m2.content LIKE ? COLLATE NOCASE
            )
          )
        """
        params += [like, like]
    cur = await db.execute(
        f"""
        SELECT id, title, model, updated_at, pinned, archived, folder_id,
               (SELECT GROUP_CONCAT(tag, ',') FROM conversation_tags t
                WHERE t.conversation_id = conversations.id) AS tags
        FROM conversations
        WHERE user_id = ?
          AND EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = conversations.id)
          {search_sql}
        ORDER BY pinned DESC, updated_at DESC
        """,
        params,
    )
    rows = await cur.fetchall()
    return [
        ConversationSummary(
            id=r[0], title=r[1], model=r[2], updated_at=r[3],
            pinned=bool(r[4]), archived=bool(r[5]), folder_id=r[6],
            tags=sorted(r[7].split(",")) if r[7] else [],
        )
        for r in rows
    ]


@router.post("", response_model=ConversationSummary)
async def create_conversation(
    body: CreateBody, request: Request, user: dict = Depends(current_user)
):
    db = _db(request)
    if body.model is not None and not await can_access_model(db, user, body.model):
        raise HTTPException(
            status_code=403, detail=f"model '{body.model}' is not available to you"
        )
    cid = uuid.uuid4().hex
    now = int(time.time())
    await db.execute(
        "INSERT INTO conversations (id, user_id, title, model, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (cid, user["id"], "new chat", body.model, now, now),
    )
    await db.commit()
    return ConversationSummary(id=cid, title="new chat", model=body.model, updated_at=now)


@router.post("/{cid}/clone", response_model=ConversationSummary)
async def clone_conversation(
    cid: str, request: Request, user: dict = Depends(current_user)
):
    """Duplicate a conversation into a fresh one owned by the same user: copies
    settings, the active (visible) messages, tags and attached collections.
    Archived message variants are not carried over."""
    db = _db(request)
    await _owned(db, cid, user["id"])
    cur = await db.execute(
        """
        SELECT title, model, system_prompt, temperature, top_p, stop, web_search,
               tools_enabled, max_tokens, presence_penalty, frequency_penalty, seed
        FROM conversations WHERE id = ?
        """,
        (cid,),
    )
    src = await cur.fetchone()
    if not src:
        raise HTTPException(status_code=404, detail="conversation not found")

    new_id = uuid.uuid4().hex
    now = int(time.time())
    new_title = f"{src[0]} (copy)"[:200]
    await db.execute(
        """
        INSERT INTO conversations
            (id, user_id, title, model, system_prompt, temperature, top_p, stop,
             web_search, tools_enabled, max_tokens, presence_penalty,
             frequency_penalty, seed, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (new_id, user["id"], new_title, *src[1:], now, now),
    )
    # Copy the visible thread (active messages only), preserving order + timestamps.
    cur = await db.execute(
        "SELECT role, content, sources, created_at FROM messages "
        "WHERE conversation_id = ? AND active = 1 ORDER BY id",
        (cid,),
    )
    for role, content, sources, created in await cur.fetchall():
        # Give the clone its own copies of any referenced blobs so it is
        # self-contained (deleting the original can't break the clone's images).
        content = await clone_file_refs(db, user["id"], new_id, content)
        await db.execute(
            "INSERT INTO messages (conversation_id, role, content, sources, active, created_at) "
            "VALUES (?, ?, ?, ?, 1, ?)",
            (new_id, role, content, sources, created),
        )
    # Carry over organization: tags and attached knowledge collections.
    await db.execute(
        "INSERT INTO conversation_tags (conversation_id, tag) "
        "SELECT ?, tag FROM conversation_tags WHERE conversation_id = ?",
        (new_id, cid),
    )
    await db.execute(
        "INSERT INTO conversation_collections (conversation_id, collection_id) "
        "SELECT ?, collection_id FROM conversation_collections WHERE conversation_id = ?",
        (new_id, cid),
    )
    await db.commit()
    return ConversationSummary(
        id=new_id, title=new_title, model=src[1], updated_at=now,
        tags=sorted(r[0] for r in await (await db.execute(
            "SELECT tag FROM conversation_tags WHERE conversation_id = ?", (new_id,)
        )).fetchall()),
    )


@router.get("/{cid}", response_model=Conversation)
async def get_conversation(
    cid: str, request: Request, user: dict = Depends(current_user)
):
    db = _db(request)
    await _owned(db, cid, user["id"])
    cur = await db.execute(
        """
        SELECT id, title, model, system_prompt, temperature, top_p, stop,
               web_search, tools_enabled, created_at, updated_at,
               max_tokens, presence_penalty, frequency_penalty, seed, folder_id
        FROM conversations WHERE id = ?
        """,
        (cid,),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="conversation not found")
    cur = await db.execute(
        """
        SELECT m.id, m.role, m.content, m.created_at, fb.rating, m.sources
        FROM messages m
        LEFT JOIN message_feedback fb
               ON fb.message_id = m.id AND fb.user_id = ?
        WHERE m.conversation_id = ? AND m.active = 1
        ORDER BY m.id
        """,
        (user["id"], cid),
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
        web_search=bool(row[7]),
        tools_enabled=bool(row[8]),
        created_at=row[9],
        updated_at=row[10],
        max_tokens=row[11],
        presence_penalty=row[12],
        frequency_penalty=row[13],
        seed=row[14],
        folder_id=row[15],
        messages=[
            StoredMessage(
                id=m[0], role=m[1], content=m[2], created_at=m[3], rating=m[4],
                sources=json.loads(m[5]) if m[5] else None,
            )
            for m in msg_rows
        ],
    )


@router.patch("/{cid}", response_model=Conversation)
async def update_conversation(
    cid: str, body: UpdateBody, request: Request, user: dict = Depends(current_user)
):
    db = _db(request)
    await _owned(db, cid, user["id"])
    fields = body.model_dump(exclude_unset=True)
    if fields.get("model") is not None and not await can_access_model(db, user, fields["model"]):
        raise HTTPException(
            status_code=403, detail=f"model '{fields['model']}' is not available to you"
        )
    if fields.get("folder_id") is not None:
        cur = await db.execute(
            "SELECT 1 FROM folders WHERE id = ? AND user_id = ?",
            (fields["folder_id"], user["id"]),
        )
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="folder not found")
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


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")
    return cleaned or "conversation"


def _format_markdown(conv: dict) -> str:
    out: list[str] = [f"# {conv['title']}", ""]
    if conv.get("system_prompt"):
        out += ["**system prompt:**", "", "> " + conv["system_prompt"].replace("\n", "\n> "), ""]
    for m in conv["messages"]:
        ts = datetime.datetime.fromtimestamp(m["created_at"]).isoformat(timespec="seconds")
        content = m["content"]
        if isinstance(content, list):
            text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
            img_parts = [p for p in content if p.get("type") == "image_url"]
            content_str = "\n".join(text_parts)
            if img_parts:
                content_str += "\n\n" + "\n".join(
                    f"![attachment]({p['image_url']['url'][:80]}…)" for p in img_parts
                )
        else:
            content_str = content
        out += [f"## {m['role']}  _{ts}_", "", content_str, ""]
    return "\n".join(out)


@router.get("/{cid}/export")
async def export_conversation(
    cid: str,
    request: Request,
    user: dict = Depends(current_user),
    format: str = Query("json", pattern="^(json|md)$"),
):
    conv = await get_conversation(cid, request, user)
    conv_dict = conv.model_dump()
    # decode multimodal contents for export readability
    for m in conv_dict["messages"]:
        m["content"] = _decode_content(m["content"])
    base = _safe_filename(conv_dict["title"])
    if format == "md":
        body = _format_markdown(conv_dict).encode("utf-8")
        return Response(
            content=body,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{base}.md"'},
        )
    import json as _json
    body = _json.dumps(conv_dict, indent=2).encode("utf-8")
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{base}.json"'},
    )


async def _maybe_update_title(
    db: aiosqlite.Connection, cid: str, title: str, first_user: str
) -> None:
    if title == "new chat":
        new_title = first_user[:60].replace("\n", " ").strip() or "new chat"
        await db.execute(
            "UPDATE conversations SET title = ? WHERE id = ?", (new_title, cid)
        )


def _clean_title(text: str) -> str:
    """Reduce a model's title reply to a single clean line, <= 60 chars."""
    stripped = text.strip()
    if not stripped:
        return ""
    line = stripped.splitlines()[0].strip().strip('"').strip("'").strip()
    return line.rstrip(".").strip()[:60].strip()


async def _maybe_update_model(
    db: aiosqlite.Connection, cid: str, requested: str | None, current: str | None
) -> str:
    chosen = requested or current or settings.default_model
    if requested and requested != current:
        await db.execute(
            "UPDATE conversations SET model = ? WHERE id = ?", (requested, cid)
        )
    return chosen


async def _guard_model(
    db: aiosqlite.Connection, user: dict, requested: str | None, current: str | None
) -> None:
    """Reject (403) before any mutation if the user may not use the chosen model."""
    candidate = requested or current or settings.default_model
    if not await can_access_model(db, user, candidate):
        raise HTTPException(
            status_code=403, detail=f"model '{candidate}' is not available to you"
        )


@router.post("/{cid}/messages")
async def send_message(
    cid: str, body: SendBody, request: Request, user: dict = Depends(current_user)
) -> StreamingResponse:
    db = _db(request)
    await _owned(db, cid, user["id"])
    http = _http(request)

    conv = await _conv_settings(db, cid)
    await _guard_model(db, user, body.model, conv["model"])
    history = await _load_history(db, cid)

    now = int(time.time())
    stored_content = await externalize_parts(db, user["id"], cid, body.content)
    await db.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (cid, "user", _encode_content(stored_content), now),
    )
    await _maybe_update_title(db, cid, conv["title"], _content_preview(body.content))
    chosen_model = await _maybe_update_model(db, cid, body.model, conv["model"])
    await db.commit()

    query_text = _content_text(body.content)
    context, sources = await _gather_context(db, http, cid, query_text, conv, user["id"])
    upstream = _build_upstream_messages(
        conv["system_prompt"],
        context,
        history,
        [{"role": "user", "content": body.content}],
    )
    conn = await resolve_connection(request, db, chosen_model)
    return StreamingResponse(
        _stream_and_persist(
            db, http, cid, upstream, chosen_model,
            conv["temperature"], conv["top_p"], conv["stop"], tools_enabled=conv["tools_enabled"], user_id=user["id"],
            plugins=getattr(request.app.state, "plugins", None),
            conn=conn,
            gen=conv,
            sources=sources,
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
    await _guard_model(db, user, body.model, conv["model"])

    cur = await db.execute(
        "SELECT id, role FROM messages WHERE conversation_id = ? AND active = 1 "
        "ORDER BY id DESC LIMIT 1",
        (cid,),
    )
    last = await cur.fetchone()
    if not last or last[1] != "assistant":
        raise HTTPException(
            status_code=400, detail="nothing to regenerate (no trailing assistant message)"
        )
    return await _regenerate_assistant(request, db, http, cid, conv, user, last[0], body.model)


@router.post("/{cid}/messages/{msg_id}/regenerate")
async def regenerate_message(
    cid: str, msg_id: int, body: RegenerateBody, request: Request,
    user: dict = Depends(current_user),
) -> StreamingResponse:
    """Regenerate a specific (not necessarily trailing) assistant turn. Any turns
    after it are discarded — regenerating mid-thread branches the conversation."""
    db = _db(request)
    await _owned(db, cid, user["id"])
    http = _http(request)
    conv = await _conv_settings(db, cid)
    await _guard_model(db, user, body.model, conv["model"])

    cur = await db.execute(
        "SELECT role FROM messages WHERE id = ? AND conversation_id = ? AND active = 1",
        (msg_id, cid),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="message not found")
    if row[0] != "assistant":
        raise HTTPException(status_code=400, detail="only assistant messages can be regenerated")
    # Discard genuinely-later turns (id beyond this turn's variant chain tip),
    # but keep this turn's own variants (ids within the chain) so navigation
    # still works after the regenerate.
    chain = await _variant_chain(db, cid, msg_id)
    tip_id = chain[-1]["id"] if chain else msg_id
    await db.execute(
        "DELETE FROM messages WHERE conversation_id = ? AND id > ?", (cid, tip_id)
    )
    await gc_orphan_files(db, cid)
    return await _regenerate_assistant(request, db, http, cid, conv, user, msg_id, body.model)


_CONTINUE_INSTRUCTION = (
    "Continue your previous message. Resume from exactly where it ended and do "
    "not repeat any of it — output only the additional text."
)


@router.post("/{cid}/messages/{msg_id}/continue")
async def continue_message(
    cid: str, msg_id: int, body: RegenerateBody, request: Request,
    user: dict = Depends(current_user),
) -> StreamingResponse:
    """Continue (extend) the trailing assistant reply that stopped early — e.g.
    cut off by max_tokens. The streamed text is appended onto the existing
    message rather than starting a new turn.

    Portable across OpenAI-compatible upstreams: the partial reply is replayed as
    history and a continuation instruction is appended, so any provider can pick
    up where it left off (no provider-specific assistant-prefix API required)."""
    db = _db(request)
    await _owned(db, cid, user["id"])
    http = _http(request)
    conv = await _conv_settings(db, cid)
    await _guard_model(db, user, body.model, conv["model"])

    cur = await db.execute(
        "SELECT id, role, content FROM messages WHERE conversation_id = ? AND active = 1 "
        "ORDER BY id DESC LIMIT 1",
        (cid,),
    )
    last = await cur.fetchone()
    if not last or last[0] != msg_id or last[1] != "assistant":
        raise HTTPException(
            status_code=400, detail="can only continue the latest assistant message"
        )
    if not isinstance(_decode_content(last[2]), str) or not last[2].strip():
        raise HTTPException(
            status_code=400, detail="cannot continue a non-text or empty message"
        )

    chosen_model = await _maybe_update_model(db, cid, body.model, conv["model"])
    await db.commit()
    # History already ends with the partial assistant reply; nudge the model to
    # extend it. tools/web/RAG are intentionally off — continue is plain prose.
    history = await _load_history(db, cid)
    upstream = _build_upstream_messages(
        conv["system_prompt"], None, history,
        [{"role": "user", "content": _CONTINUE_INSTRUCTION}],
    )
    conn = await resolve_connection(request, db, chosen_model)
    return StreamingResponse(
        _stream_and_persist(
            db, http, cid, upstream, chosen_model,
            conv["temperature"], conv["top_p"], conv["stop"],
            tools_enabled=False, user_id=user["id"],
            plugins=getattr(request.app.state, "plugins", None),
            conn=conn, gen=conv, continue_message_id=msg_id,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _regenerate_assistant(
    request: Request, db: aiosqlite.Connection, http: httpx.AsyncClient,
    cid: str, conv: dict, user: dict, archived_id: int, body_model: str | None,
) -> StreamingResponse:
    # Non-destructive: archive the superseded assistant variant (active=0) rather
    # than deleting it, so the prior reply is preserved.
    #
    # Keep the variant chain strictly linear: attach the new variant to the
    # current TIP of the chain (the highest-id member), NOT necessarily the
    # active node — the user may have navigated back to an older variant. Always
    # parenting at the active node would fork a node that already has a child,
    # producing two active variants and a chain _variant_chain can't walk.
    chain = await _variant_chain(db, cid, archived_id)
    chain_ids = [v["id"] for v in chain] or [archived_id]
    tip_id = chain_ids[-1]
    placeholders = ",".join("?" * len(chain_ids))
    await db.execute(
        f"UPDATE messages SET active = 0 WHERE conversation_id = ? AND id IN ({placeholders})",
        (cid, *chain_ids),
    )
    archived_id = tip_id
    chosen_model = await _maybe_update_model(db, cid, body_model, conv["model"])
    await db.commit()

    history = await _load_history(db, cid)
    last_user_text = ""
    for m in reversed(history):
        if m["role"] == "user":
            last_user_text = _content_text(m["content"])
            break
    context, sources = await _gather_context(db, http, cid, last_user_text, conv, user["id"])
    upstream = _build_upstream_messages(conv["system_prompt"], context, history)
    conn = await resolve_connection(request, db, chosen_model)
    return StreamingResponse(
        _stream_and_persist(
            db, http, cid, upstream, chosen_model,
            conv["temperature"], conv["top_p"], conv["stop"], tools_enabled=conv["tools_enabled"], user_id=user["id"],
            plugins=getattr(request.app.state, "plugins", None),
            parent_message_id=archived_id,
            conn=conn,
            gen=conv,
            sources=sources,
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
    await _guard_model(db, user, body.model, conv["model"])

    stored_content = await externalize_parts(db, user["id"], cid, body.content)
    await db.execute(
        "UPDATE messages SET content = ? WHERE id = ?", (_encode_content(stored_content), msg_id)
    )
    await db.execute(
        "DELETE FROM messages WHERE conversation_id = ? AND id > ?", (cid, msg_id)
    )
    await gc_orphan_files(db, cid)
    await _maybe_update_title(db, cid, conv["title"], _content_preview(body.content))
    chosen_model = await _maybe_update_model(db, cid, body.model, conv["model"])
    await db.commit()

    history = await _load_history(db, cid)
    query_text = _content_text(body.content)
    context, sources = await _gather_context(db, http, cid, query_text, conv, user["id"])
    upstream = _build_upstream_messages(conv["system_prompt"], context, history)
    conn = await resolve_connection(request, db, chosen_model)
    return StreamingResponse(
        _stream_and_persist(
            db, http, cid, upstream, chosen_model,
            conv["temperature"], conv["top_p"], conv["stop"], tools_enabled=conv["tools_enabled"], user_id=user["id"],
            plugins=getattr(request.app.state, "plugins", None),
            conn=conn,
            gen=conv,
            sources=sources,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/{cid}/messages/{msg_id}")
async def delete_message(
    cid: str, msg_id: int, request: Request, user: dict = Depends(current_user)
) -> dict:
    """Delete a message and everything after it (truncate the thread here),
    including any archived variants. Keeps history coherent — no dangling reply
    to a removed turn."""
    db = _db(request)
    await _owned(db, cid, user["id"])
    cur = await db.execute(
        "SELECT 1 FROM messages WHERE id = ? AND conversation_id = ?", (msg_id, cid)
    )
    if not await cur.fetchone():
        raise HTTPException(status_code=404, detail="message not found")
    await db.execute(
        "DELETE FROM messages WHERE conversation_id = ? AND id >= ?", (cid, msg_id)
    )
    await gc_orphan_files(db, cid)
    await db.execute(
        "UPDATE conversations SET updated_at = ? WHERE id = ?", (int(time.time()), cid)
    )
    await db.commit()
    return {"ok": True}


@router.post("/{cid}/autotitle")
async def autotitle(
    cid: str, request: Request, user: dict = Depends(current_user)
) -> dict:
    """Generate a short conversation title from the first exchange via a
    lightweight upstream call. No-op (returns the current title) when disabled,
    when there's no exchange yet, or on any upstream failure."""
    db = _db(request)
    await _owned(db, cid, user["id"])
    http = _http(request)
    conv = await _conv_settings(db, cid)
    if not settings.auto_title:
        return {"title": conv["title"]}

    history = await _load_history(db, cid)
    convo = [m for m in history if m["role"] in ("user", "assistant")]
    if len(convo) < 2:
        return {"title": conv["title"]}
    # Don't run inference on a model the user may not use (e.g. one that was
    # restricted after this conversation was created). Matches the chat guards.
    await _guard_model(db, user, None, conv["model"])

    # Text-only view of the opening turns (don't ship images to the titler).
    snippet = [
        {"role": m["role"], "content": _content_text(m["content"])} for m in convo[:4]
    ]
    instruction = {
        "role": "user",
        "content": (
            "Summarise this conversation as a short title of 3-6 words. "
            "Reply with ONLY the title — no quotes and no trailing punctuation."
        ),
    }
    body = {
        "model": conv["model"] or settings.default_model,
        "messages": [*snippet, instruction],
        "stream": True,
        "temperature": 0.2,
    }
    conn = await resolve_connection(request, db, conv["model"])
    text = ""
    try:
        async with http.stream(
            "POST", conn_url(conn, "chat/completions"), json=body, headers=conn_headers(conn)
        ) as r:
            if r.status_code >= 400:
                return {"title": conv["title"]}
            async for line in r.aiter_lines():
                if line.endswith("[DONE]"):
                    break
                if not line.startswith("data: "):
                    continue
                try:
                    chunk = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                delta = (chunk.get("choices") or [{}])[0].get("delta", {}).get("content")
                if isinstance(delta, str):
                    text += delta
    except httpx.HTTPError:
        return {"title": conv["title"]}

    new_title = _clean_title(text) or conv["title"]
    await db.execute("UPDATE conversations SET title = ? WHERE id = ?", (new_title, cid))
    await db.commit()
    return {"title": new_title}


@router.post("/{cid}/followups")
async def followups(cid: str, request: Request, user: dict = Depends(current_user)) -> dict:
    """Suggest a few follow-up questions from the recent exchange via a
    lightweight upstream call. No-op (empty) when disabled, with no exchange yet,
    or on any failure."""
    db = _db(request)
    await _owned(db, cid, user["id"])
    http = _http(request)
    conv = await _conv_settings(db, cid)
    if not settings.suggest_followups:
        return {"suggestions": []}
    history = await _load_history(db, cid)
    convo = [m for m in history if m["role"] in ("user", "assistant")]
    if len(convo) < 2:
        return {"suggestions": []}
    await _guard_model(db, user, None, conv["model"])

    snippet = [{"role": m["role"], "content": _content_text(m["content"])} for m in convo[-4:]]
    instruction = {
        "role": "user",
        "content": (
            "Suggest exactly 3 short follow-up questions the user might ask next, "
            "each under 12 words. Reply with one question per line and nothing "
            "else — no numbering, no preamble."
        ),
    }
    conn = await resolve_connection(request, db, conv["model"])
    body = {
        "model": conv["model"] or settings.default_model,
        "messages": [*snippet, instruction],
        "stream": True,
        "temperature": 0.7,
        "max_tokens": 200,
    }
    text = ""
    try:
        async with http.stream(
            "POST", conn_url(conn, "chat/completions"), json=body, headers=conn_headers(conn)
        ) as r:
            if r.status_code >= 400:
                return {"suggestions": []}
            async for line in r.aiter_lines():
                if line.endswith("[DONE]"):
                    break
                if not line.startswith("data: "):
                    continue
                try:
                    chunk = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                delta = (chunk.get("choices") or [{}])[0].get("delta", {}).get("content")
                if isinstance(delta, str):
                    text += delta
    except httpx.HTTPError:
        return {"suggestions": []}

    suggestions: list[str] = []
    for raw in text.splitlines():
        s = raw.strip().lstrip("-•*0123456789.) \t").strip()
        if s:
            suggestions.append(s)
    return {"suggestions": suggestions[:3]}


class TagsBody(BaseModel):
    tags: list[str]


@router.get("/{cid}/tags")
async def get_tags(cid: str, request: Request, user: dict = Depends(current_user)) -> dict:
    db = _db(request)
    await _owned(db, cid, user["id"])
    cur = await db.execute(
        "SELECT tag FROM conversation_tags WHERE conversation_id = ? ORDER BY tag", (cid,)
    )
    return {"tags": [r[0] for r in await cur.fetchall()]}


@router.put("/{cid}/tags")
async def set_tags(
    cid: str, body: TagsBody, request: Request, user: dict = Depends(current_user)
) -> dict:
    db = _db(request)
    await _owned(db, cid, user["id"])
    clean: list[str] = []
    seen: set[str] = set()
    for raw in body.tags:
        t = raw.strip()[:40]
        if t and t.lower() not in seen:
            seen.add(t.lower())
            clean.append(t)
        if len(clean) >= 20:
            break
    await db.execute("DELETE FROM conversation_tags WHERE conversation_id = ?", (cid,))
    for t in clean:
        await db.execute(
            "INSERT INTO conversation_tags (conversation_id, tag) VALUES (?, ?)", (cid, t)
        )
    await db.commit()
    return {"tags": clean}


class FeedbackBody(BaseModel):
    rating: int = Field(ge=-1, le=1)  # 1 = thumbs up, -1 = thumbs down, 0 = clear
    comment: str | None = Field(default=None, max_length=2000)


@router.put("/{cid}/messages/{msg_id}/feedback")
async def set_feedback(
    cid: str,
    msg_id: int,
    body: FeedbackBody,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    """Record (or clear, when rating=0) the current user's thumbs up/down on a
    message. Upsert: re-rating updates rather than duplicates."""
    db = _db(request)
    await _owned(db, cid, user["id"])
    cur = await db.execute(
        "SELECT 1 FROM messages WHERE id = ? AND conversation_id = ?", (msg_id, cid)
    )
    if not await cur.fetchone():
        raise HTTPException(status_code=404, detail="message not found")
    now = int(time.time())
    if body.rating == 0:
        await db.execute(
            "DELETE FROM message_feedback WHERE message_id = ? AND user_id = ?",
            (msg_id, user["id"]),
        )
        await db.commit()
        return {"rating": None}
    await db.execute(
        """
        INSERT INTO message_feedback
            (message_id, user_id, rating, comment, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(message_id, user_id) DO UPDATE SET
            rating = excluded.rating,
            comment = excluded.comment,
            updated_at = excluded.updated_at
        """,
        (msg_id, user["id"], body.rating, body.comment, now, now),
    )
    await db.commit()
    return {"rating": body.rating}


async def _variant_chain(db: aiosqlite.Connection, cid: str, msg_id: int) -> list[dict]:
    """Ordered regenerate-variant chain that msg_id belongs to.

    Regenerate links each new assistant variant to the one it replaced via
    parent_id, forming a linear chain (root -> … -> active tip). Given any
    member, walk up to the root then down through children.
    """
    cur = await db.execute(
        "SELECT id, parent_id FROM messages WHERE id = ? AND conversation_id = ?",
        (msg_id, cid),
    )
    row = await cur.fetchone()
    if not row:
        return []
    # Walk up to the root of the chain.
    root, pid, guard = row[0], row[1], 0
    while pid is not None and guard < 1000:
        guard += 1
        cur = await db.execute(
            "SELECT id, parent_id FROM messages WHERE id = ? AND conversation_id = ?",
            (pid, cid),
        )
        parent = await cur.fetchone()
        if not parent:
            break
        root, pid = parent[0], parent[1]
    # Walk down from the root through children (linear chain).
    chain: list[dict] = []
    node, guard = root, 0
    while node is not None and guard < 1000:
        guard += 1
        cur = await db.execute(
            "SELECT id, active, created_at FROM messages WHERE id = ? AND conversation_id = ?",
            (node, cid),
        )
        r = await cur.fetchone()
        if not r:
            break
        chain.append({"id": r[0], "active": bool(r[1]), "created_at": r[2]})
        cur = await db.execute(
            "SELECT id FROM messages WHERE parent_id = ? AND conversation_id = ?",
            (node, cid),
        )
        child = await cur.fetchone()
        node = child[0] if child else None
    return chain


@router.get("/{cid}/messages/{msg_id}/variants")
async def list_variants(
    cid: str, msg_id: int, request: Request, user: dict = Depends(current_user)
) -> dict:
    """List the regenerate-variant chain a message belongs to (oldest first)."""
    db = _db(request)
    await _owned(db, cid, user["id"])
    chain = await _variant_chain(db, cid, msg_id)
    if not chain:
        raise HTTPException(status_code=404, detail="message not found")
    return {"variants": chain}


@router.post("/{cid}/messages/{msg_id}/activate")
async def activate_variant(
    cid: str, msg_id: int, request: Request, user: dict = Depends(current_user)
) -> dict:
    """Make msg_id the active variant in its chain (the others go to active=0),
    so it's the one returned by reads. Lets the UI switch between regenerated
    replies without losing any."""
    db = _db(request)
    await _owned(db, cid, user["id"])
    chain = await _variant_chain(db, cid, msg_id)
    ids = [v["id"] for v in chain]
    if msg_id not in ids:
        raise HTTPException(status_code=404, detail="message not found")
    # Only the latest turn's variants may be switched. Activating a mid-thread
    # variant while later turns exist would leave the downstream replies hanging
    # off a now-inactive upstream turn (an inconsistent active thread).
    cur = await db.execute(
        "SELECT 1 FROM messages WHERE conversation_id = ? AND id > ?", (cid, max(ids))
    )
    if await cur.fetchone():
        raise HTTPException(
            status_code=409, detail="can only switch variants of the latest turn"
        )
    for vid in ids:
        await db.execute(
            "UPDATE messages SET active = ? WHERE id = ?", (1 if vid == msg_id else 0, vid)
        )
    await db.commit()
    return {"active": msg_id}
