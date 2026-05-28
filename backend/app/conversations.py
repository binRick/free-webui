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
from pydantic import BaseModel

from .auth import current_user
from .config import settings
from .mcp import compose_tool_specs, run_tool as run_dispatch
from .memories import load_memory_context
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
    web_search: bool = False
    tools_enabled: bool = False
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


async def _load_history(db: aiosqlite.Connection, cid: str) -> list[dict]:
    cur = await db.execute(
        "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id", (cid,)
    )
    rows = await cur.fetchall()
    return [{"role": r[0], "content": _decode_content(r[1])} for r in rows]


async def _conv_settings(db: aiosqlite.Connection, cid: str) -> dict[str, Any]:
    cur = await db.execute(
        """
        SELECT title, model, system_prompt, temperature, top_p, stop,
               web_search, tools_enabled
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
    }


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
) -> AsyncIterator[bytes]:
    """Stream a chat completion. If tools_enabled, runs a tool loop until
    the upstream finishes with a non-tool finish reason, surfacing each
    tool call to the client as an `event: tool_call` SSE frame.
    """
    msgs = list(upstream_messages)
    final_content: list[str] = []
    tools_executed: list[dict] = []

    # Compose the tool catalogue once at the start of the turn (built-in
    # + every enabled MCP server's tools/list); reused across tool loops.
    tool_specs: list[dict] = []
    dispatch: dict[str, tuple[int, str]] = {}
    if tools_enabled and user_id is not None:
        tool_specs, dispatch = await compose_tool_specs(db, user_id)

    def _payload() -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": msgs,
            "stream": True,
        }
        if temperature is not None:
            body["temperature"] = temperature
        if top_p is not None:
            body["top_p"] = top_p
        if stop:
            body["stop"] = stop
        if tools_enabled and tool_specs:
            body["tools"] = tool_specs
            body["tool_choice"] = "auto"
        return body

    try:
        for _ in range(max_tool_loops):
            iter_content: list[str] = []
            tool_buf: dict[int, dict] = {}
            saw_done = False

            async with http.stream("POST", "/chat/completions", json=_payload()) as r:
                if r.status_code >= 400:
                    err = (await r.aread()).decode(errors="replace")
                    yield f"data: {json.dumps({'error': err})}\n\n".encode()
                    return

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

                    delta = chunk.get("choices", [{}])[0].get("delta", {})
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
                result = await run_dispatch(
                    db, user_id or 0, dispatch, rec["name"], args
                )
                tools_executed.append(
                    {"name": rec["name"], "arguments": args, "result": result}
                )
                event = json.dumps(
                    {"name": rec["name"], "arguments": args, "result": result}
                )
                yield f"event: tool_call\ndata: {event}\n\n".encode()
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
        if text:
            ts = int(time.time())
            await db.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (cid, "assistant", text, ts),
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
        SELECT id, title, model, system_prompt, temperature, top_p, stop,
               web_search, tools_enabled, created_at, updated_at
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
        web_search=bool(row[7]),
        tools_enabled=bool(row[8]),
        created_at=row[9],
        updated_at=row[10],
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

    query_text = _content_text(body.content)
    rag_ctx = await retrieve_context(db, http, cid, query_text)
    web_ctx = None
    if conv.get("web_search"):
        web_ctx = format_web_context(await web_search(query_text))
    mem_ctx = await load_memory_context(db, user["id"])
    context = "\n\n".join(c for c in (mem_ctx, web_ctx, rag_ctx) if c) or None
    upstream = _build_upstream_messages(
        conv["system_prompt"],
        context,
        history,
        [{"role": "user", "content": body.content}],
    )
    return StreamingResponse(
        _stream_and_persist(
            db, http, cid, upstream, chosen_model,
            conv["temperature"], conv["top_p"], conv["stop"], tools_enabled=conv["tools_enabled"], user_id=user["id"],
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
    last_user_text = ""
    for m in reversed(history):
        if m["role"] == "user":
            last_user_text = _content_text(m["content"])
            break
    rag_ctx = await retrieve_context(db, http, cid, last_user_text)
    web_ctx = None
    if conv.get("web_search"):
        web_ctx = format_web_context(await web_search(last_user_text))
    mem_ctx = await load_memory_context(db, user["id"])
    context = "\n\n".join(c for c in (mem_ctx, web_ctx, rag_ctx) if c) or None
    upstream = _build_upstream_messages(conv["system_prompt"], context, history)
    return StreamingResponse(
        _stream_and_persist(
            db, http, cid, upstream, chosen_model,
            conv["temperature"], conv["top_p"], conv["stop"], tools_enabled=conv["tools_enabled"], user_id=user["id"],
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
    query_text = _content_text(body.content)
    rag_ctx = await retrieve_context(db, http, cid, query_text)
    web_ctx = None
    if conv.get("web_search"):
        web_ctx = format_web_context(await web_search(query_text))
    mem_ctx = await load_memory_context(db, user["id"])
    context = "\n\n".join(c for c in (mem_ctx, web_ctx, rag_ctx) if c) or None
    upstream = _build_upstream_messages(conv["system_prompt"], context, history)
    return StreamingResponse(
        _stream_and_persist(
            db, http, cid, upstream, chosen_model,
            conv["temperature"], conv["top_p"], conv["stop"], tools_enabled=conv["tools_enabled"], user_id=user["id"],
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
