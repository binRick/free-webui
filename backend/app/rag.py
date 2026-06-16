"""Chunking, embedding, and hybrid (dense + BM25) retrieval for per-chat RAG."""
from __future__ import annotations

import io
import math
import re
import struct

import aiosqlite
import httpx
import numpy as np
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


def _cosine_scores(query_vec: list[float], rows: list) -> dict[int, float]:
    """Vectorized cosine similarity of the query against every candidate chunk.

    `rows` are ``(text, embedding_blob, filename)``. Returns ``{row_index:
    cosine}`` for the rows whose embedding dimension matches the query (same
    embedding model); mismatched rows are omitted. numpy turns the former
    per-chunk Python loop into one matrix-vector product, so this stays fast as
    the corpus grows.
    """
    q = np.asarray(query_vec, dtype=np.float32)
    qn = float(np.linalg.norm(q))
    if qn == 0.0:
        return {}
    width = q.shape[0] * 4  # bytes per float32 embedding of the query's dimension
    keep: list[int] = []
    mats: list[np.ndarray] = []
    for i, (_text, blob, _fn) in enumerate(rows):
        if len(blob) == width:
            keep.append(i)
            mats.append(np.frombuffer(blob, dtype="<f4"))
    if not mats:
        return {}
    matrix = np.vstack(mats)  # (k, dim)
    norms = np.linalg.norm(matrix, axis=1)
    norms[norms == 0.0] = 1.0
    sims = (matrix @ q) / (norms * qn)  # cosine similarity per chunk
    return {keep[j]: float(sims[j]) for j in range(len(keep))}


def _rank_chunks(
    query_vec: list[float], rows: list, top_k: int
) -> list[tuple[float, str, str]]:
    """Dense-only cosine top-k over candidate chunks. Returns ``(score,
    filename, text)`` sorted by score descending."""
    sims = _cosine_scores(query_vec, rows)
    if not sims:
        return []
    idxs = list(sims.keys())
    arr = np.asarray([sims[i] for i in idxs], dtype=np.float32)
    k = min(top_k, arr.shape[0])
    part = np.argpartition(-arr, k - 1)[:k]
    order = part[np.argsort(-arr[part])]
    return [(float(arr[p]), rows[idxs[p]][2], rows[idxs[p]][0]) for p in order]


# ---------- sparse (BM25) scoring + hybrid fusion ----------

_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_BM25_K1 = 1.5
_BM25_B = 0.75
_RRF_K = 60  # Reciprocal Rank Fusion damping constant (standard default)


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _bm25_scores(query: str, docs: list[str]) -> list[float]:
    """Okapi BM25 of the query against each doc, scored over `docs` as the
    corpus. A doc with no query-term overlap scores 0.0. Pure-Python: the corpus
    here is one conversation's attached chunks, so tokenizing per query is cheap.
    """
    q_terms = set(_tokenize(query))
    if not q_terms or not docs:
        return [0.0] * len(docs)
    tfs: list[dict[str, int]] = []
    lengths: list[int] = []
    for d in docs:
        counts: dict[str, int] = {}
        for tok in _tokenize(d):
            counts[tok] = counts.get(tok, 0) + 1
        tfs.append(counts)
        lengths.append(sum(counts.values()))
    n_docs = len(docs)
    avgdl = (sum(lengths) / n_docs) or 1.0
    df = {term: sum(1 for c in tfs if term in c) for term in q_terms}
    scores: list[float] = []
    for i, counts in enumerate(tfs):
        s = 0.0
        for term in q_terms:
            f = counts.get(term, 0)
            if not f:
                continue
            n_q = df[term]
            # idf with +1 smoothing so it is always positive (no negative weight
            # for terms appearing in over half the corpus).
            idf = math.log(1 + (n_docs - n_q + 0.5) / (n_q + 0.5))
            denom = f + _BM25_K1 * (1 - _BM25_B + _BM25_B * lengths[i] / avgdl)
            s += idf * f * (_BM25_K1 + 1) / denom
        scores.append(s)
    return scores


def _hybrid_rank(
    query: str, query_vec: list[float], rows: list, top_k: int, use_bm25: bool
) -> list[tuple[float, str, str]]:
    """Fuse a dense (cosine) ranking with a sparse (BM25) ranking via Reciprocal
    Rank Fusion and return the top-k ``(cosine_score, filename, text)``.

    RRF is scale-free — it ranks by ``sum 1/(k + rank)`` across both lists — so
    the unbounded BM25 scale and the bounded cosine scale never need
    normalising. Each list contributes only its positively-scored docs, so a
    chunk surfaces if it is a strong semantic OR keyword match. With ``use_bm25``
    off this degrades to dense-only. The reported score is the cosine similarity
    (0.0 for a keyword-only match), for transparency in the context block.
    """
    sims = _cosine_scores(query_vec, rows)
    vec_list = sorted((i for i in sims if sims[i] > 0.0), key=lambda i: sims[i], reverse=True)

    if not use_bm25:
        chosen = vec_list[:top_k]
        return [(sims[i], rows[i][2], rows[i][0]) for i in chosen]

    bm = _bm25_scores(query, [r[0] for r in rows])
    bm_list = sorted((i for i in range(len(rows)) if bm[i] > 0.0), key=lambda i: bm[i], reverse=True)

    fused: dict[int, float] = {}
    for rank, i in enumerate(vec_list):
        fused[i] = fused.get(i, 0.0) + 1.0 / (_RRF_K + rank + 1)
    for rank, i in enumerate(bm_list):
        fused[i] = fused.get(i, 0.0) + 1.0 / (_RRF_K + rank + 1)
    if not fused:
        return []
    chosen = sorted(fused, key=lambda i: fused[i], reverse=True)[:top_k]
    return [(sims.get(i, 0.0), rows[i][2], rows[i][0]) for i in chosen]


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

    top = _hybrid_rank(query, query_vec, rows, top_k, use_bm25=settings.rag_hybrid)
    if not top:
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
