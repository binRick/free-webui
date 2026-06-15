"""Chunking, embedding, and brute-force cosine retrieval for per-chat RAG."""
from __future__ import annotations

import io
import math
import struct

import aiosqlite
import httpx
from fastapi import HTTPException

from .config import settings
from .connections import config_connection, conn_headers, conn_url


# ---------- text extraction ----------

_TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst",
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".svelte",
    ".go", ".rs", ".java", ".kt", ".swift", ".c", ".cc", ".cpp", ".h", ".hpp",
    ".rb", ".php", ".lua", ".sh", ".bash", ".zsh", ".fish",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".csv", ".xml", ".html", ".css",
    ".sql", ".dockerfile",
}


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader  # local import to keep cold startup light
    reader = PdfReader(io.BytesIO(data))
    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n\n".join(p for p in parts if p)


def extract_text(filename: str, mime: str | None, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf") or (mime and "pdf" in mime):
        return _extract_pdf(data)
    if mime and mime.startswith("text/"):
        return data.decode("utf-8", errors="replace")
    for ext in _TEXT_EXTENSIONS:
        if name.endswith(ext):
            return data.decode("utf-8", errors="replace")
    # Fallback — try utf-8 anyway, raise on binary garbage.
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=415,
            detail=f"unsupported file type: {filename}",
        )


# ---------- chunking ----------

def chunk_text(text: str, size: int | None = None, overlap: int | None = None) -> list[str]:
    """Fixed-size character chunks with overlap. Skips empty/whitespace chunks."""
    size = size or settings.chunk_size
    overlap = overlap or settings.chunk_overlap
    if overlap >= size:
        overlap = size // 4
    step = max(size - overlap, 1)
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        piece = text[i : i + size].strip()
        if piece:
            out.append(piece)
        i += step
    return out


# ---------- embedding pack / unpack ----------

def pack(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack(b: bytes) -> list[float]:
    n = len(b) // 4
    return list(struct.unpack(f"<{n}f", b))


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


# ---------- upstream embeddings ----------

async def embed_texts(
    http: httpx.AsyncClient, texts: list[str], model: str | None = None
) -> list[list[float]]:
    if not texts:
        return []
    model = model or settings.embedding_model
    # Embeddings always use the env-configured (default) connection. Pass its
    # auth explicitly — the shared client carries no default Authorization.
    conn = config_connection()
    try:
        r = await http.post(
            conn_url(conn, "embeddings"),
            json={"model": model, "input": texts},
            headers=conn_headers(conn),
        )
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=502, detail=f"upstream embeddings unreachable: {e}"
        )
    if r.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"upstream embeddings error ({r.status_code}): {r.text[:200]}",
        )
    body = r.json()
    data = body.get("data", [])
    data_sorted = sorted(data, key=lambda x: x.get("index", 0))
    return [item["embedding"] for item in data_sorted]


async def prepare_document(
    http: httpx.AsyncClient, filename: str, content_type: str | None, data: bytes
) -> tuple[list[str], list[list[float]]]:
    """Extract, chunk, and embed a file. Shared by per-chat uploads and
    knowledge-base collections. Raises HTTPException on bad input/upstream."""
    text = extract_text(filename, content_type, data)
    chunks = chunk_text(text)
    if not chunks:
        raise HTTPException(status_code=400, detail="no extractable text in file")
    embeddings = await embed_texts(http, chunks)
    if len(embeddings) != len(chunks):
        raise HTTPException(status_code=502, detail="upstream returned wrong number of embeddings")
    return chunks, embeddings


# ---------- retrieval ----------

async def retrieve_context(
    db: aiosqlite.Connection,
    http: httpx.AsyncClient,
    cid: str,
    query: str,
    top_k: int | None = None,
) -> tuple[str | None, list[dict]]:
    """Embed the query, score the conversation's chunks (own uploads + attached
    collections), and return (context_block, sources). sources is a list of
    {kind: "document", label: filename}. (None, []) if nothing matched."""
    if not query.strip():
        return None, []
    top_k = top_k or settings.rag_top_k

    # The conversation's own uploads...
    cur = await db.execute(
        """
        SELECT c.text, c.embedding, d.filename
        FROM chunks c JOIN documents d ON d.id = c.document_id
        WHERE d.conversation_id = ?
        """,
        (cid,),
    )
    rows = list(await cur.fetchall())
    # ...plus every attached knowledge-base collection.
    cur = await db.execute(
        """
        SELECT cc.text, cc.embedding, cd.filename
        FROM collection_chunks cc
        JOIN collection_documents cd ON cd.id = cc.document_id
        WHERE cd.collection_id IN (
            SELECT collection_id FROM conversation_collections WHERE conversation_id = ?
        )
        """,
        (cid,),
    )
    rows += list(await cur.fetchall())
    if not rows:
        return None, []

    [query_vec] = await embed_texts(http, [query])

    scored: list[tuple[float, str, str]] = []
    for text, blob, filename in rows:
        v = unpack(blob)
        scored.append((cosine(query_vec, v), filename, text))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]
    if not top or top[0][0] <= 0.0:
        return None, []

    sections = [
        f'--- from "{fn}" (score={score:.2f}) ---\n{txt}'
        for score, fn, txt in top
    ]
    sources: list[dict] = []
    seen: set[str] = set()
    for _score, fn, _txt in top:
        if fn not in seen:
            seen.add(fn)
            sources.append({"kind": "document", "label": fn})
    context = (
        "You have access to the following excerpts from documents the user attached "
        "to this conversation. Use them when answering, and say so if the answer "
        "isn't supported by the excerpts.\n\n" + "\n\n".join(sections)
    )
    return context, sources
