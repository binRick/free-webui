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


# Office Open XML files are ZIP archives of XML parts. We pull text with the
# stdlib (zipfile + ElementTree) — no native/office dependency. Guard against
# decompression bombs: cap each member's declared uncompressed size and the
# total extracted text.
_OOXML_MEMBER_MAX = 80 * 1024 * 1024  # per-part uncompressed cap
_OOXML_TEXT_MAX = 16 * 1024 * 1024     # total extracted text cap


def _ooxml_member(zf, name: str) -> bytes | None:
    import zipfile

    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.file_size > _OOXML_MEMBER_MAX:
        raise HTTPException(status_code=413, detail="document part too large")
    try:
        return zf.read(name)
    except (zipfile.BadZipFile, RuntimeError):
        return None


def _local_texts(xml_bytes: bytes, localname: str) -> list[str]:
    """All text content of elements whose namespace-stripped tag == localname."""
    import xml.etree.ElementTree as ET

    out: list[str] = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return out
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == localname and el.text:
            out.append(el.text)
    return out


def _extract_docx(data: bytes) -> str:
    import zipfile

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        doc = _ooxml_member(zf, "word/document.xml")
        if doc is None:
            return ""
        # Each <w:p> is a paragraph; join its <w:t> runs, then paragraphs by line.
        import xml.etree.ElementTree as ET

        try:
            root = ET.fromstring(doc)
        except ET.ParseError:
            return ""
        lines: list[str] = []
        for para in root.iter():
            if para.tag.rsplit("}", 1)[-1] != "p":
                continue
            runs = [el.text for el in para.iter() if el.tag.rsplit("}", 1)[-1] == "t" and el.text]
            if runs:
                lines.append("".join(runs))
        return "\n".join(lines)


def _extract_xlsx(data: bytes) -> str:
    import zipfile

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        shared: list[str] = []
        ss = _ooxml_member(zf, "xl/sharedStrings.xml")
        if ss is not None:
            import xml.etree.ElementTree as ET

            try:
                root = ET.fromstring(ss)
                for si in root:
                    runs = [el.text for el in si.iter() if el.tag.rsplit("}", 1)[-1] == "t" and el.text]
                    shared.append("".join(runs))
            except ET.ParseError:
                pass
        sheets = sorted(n for n in zf.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
        out: list[str] = []
        import xml.etree.ElementTree as ET

        for sheet in sheets:
            raw = _ooxml_member(zf, sheet)
            if raw is None:
                continue
            try:
                root = ET.fromstring(raw)
            except ET.ParseError:
                continue
            for row in root.iter():
                if row.tag.rsplit("}", 1)[-1] != "row":
                    continue
                cells: list[str] = []
                for c in row:
                    if c.tag.rsplit("}", 1)[-1] != "c":
                        continue
                    ctype = c.get("t")
                    val = "".join(
                        el.text for el in c.iter()
                        if el.tag.rsplit("}", 1)[-1] in ("v", "t") and el.text
                    )
                    if not val:
                        continue
                    if ctype == "s":  # shared-string index
                        try:
                            val = shared[int(val)]
                        except (ValueError, IndexError):
                            continue
                    cells.append(val)
                if cells:
                    out.append("\t".join(cells))
        return "\n".join(out)


def _extract_pptx(data: bytes) -> str:
    import zipfile

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        slides = sorted(
            (n for n in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
            key=lambda n: int(re.search(r"(\d+)", n).group(1)),
        )
        out: list[str] = []
        for slide in slides:
            raw = _ooxml_member(zf, slide)
            if raw is None:
                continue
            runs = _local_texts(raw, "t")  # drawingml <a:t> runs
            if runs:
                out.append("\n".join(runs))
        return "\n\n".join(out)


def extract_text(filename: str, mime: str | None, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf") or (mime and "pdf" in mime):
        return _extract_pdf(data)
    if name.endswith(".docx"):
        return _extract_docx(data)[:_OOXML_TEXT_MAX]
    if name.endswith(".xlsx"):
        return _extract_xlsx(data)[:_OOXML_TEXT_MAX]
    if name.endswith(".pptx"):
        return _extract_pptx(data)[:_OOXML_TEXT_MAX]
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


async def _extract_external(
    http: httpx.AsyncClient, filename: str, mime: str | None, data: bytes
) -> str | None:
    """POST the raw bytes to an operator-configured extraction service (Tika,
    Docling, …) and return the text, or None on any failure (caller falls back
    to the built-in extractor). Like the reranker, this only ever adds value."""
    headers = {}
    if settings.content_extraction_api_key:
        headers["authorization"] = f"Bearer {settings.content_extraction_api_key}"
    try:
        r = await http.put(
            settings.content_extraction_url,
            content=data,
            headers={**headers, "content-type": mime or "application/octet-stream", "accept": "text/plain"},
            timeout=settings.content_extraction_timeout_seconds,
        )
        if r.status_code >= 400:
            return None
        text = r.text
        return text.strip() or None
    except (httpx.HTTPError, ValueError):
        return None


async def extract_document_text(
    http: httpx.AsyncClient, filename: str, mime: str | None, data: bytes
) -> str:
    """Extract text, preferring an external extraction service when configured
    (OCR / scanned PDFs / richer Office) and falling back to the built-in
    stdlib extractor."""
    if settings.content_extraction_url:
        text = await _extract_external(http, filename, mime, data)
        if text:
            return text[:_OOXML_TEXT_MAX]
    return extract_text(filename, mime, data)


def snippet(text: str, limit: int = 320) -> str:
    """A short, single-spaced preview of an excerpt for a citation hovercard."""
    collapsed = " ".join((text or "").split())
    return collapsed if len(collapsed) <= limit else collapsed[:limit].rstrip() + "…"


def neutralize_markers(text: str) -> str:
    """Defang bracketed-number sequences inside an injected excerpt so the model
    can't echo a document's own footnote (e.g. [1]) and have the client resolve
    it to one of our citations. Display snippets are left untouched."""
    return re.sub(r"\[(\d+)\]", r"(\1)", text or "")


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


async def prepare_text(
    http: httpx.AsyncClient, text: str
) -> tuple[list[str], list[list[float]]]:
    """Chunk and embed already-extracted text. Shared by file uploads (after
    extraction) and the URL loader (which extracts HTML/PDF itself)."""
    chunks = chunk_text(text)
    if not chunks:
        raise HTTPException(status_code=400, detail="no extractable text")
    embeddings = await embed_texts(http, chunks)
    if len(embeddings) != len(chunks):
        raise HTTPException(status_code=502, detail="upstream returned wrong number of embeddings")
    return chunks, embeddings


async def prepare_document(
    http: httpx.AsyncClient, filename: str, content_type: str | None, data: bytes
) -> tuple[str, list[str], list[list[float]]]:
    """Extract, chunk, and embed a file. Returns (full_text, chunks, embeddings).
    Shared by per-chat uploads and knowledge-base collections. The full text is
    kept verbatim for full-context RAG. Raises HTTPException on bad input/upstream."""
    text = await extract_document_text(http, filename, content_type, data)
    chunks, embeddings = await prepare_text(http, text)
    return text, chunks, embeddings


# ---------- reranking ----------

async def _rerank(
    query: str, candidates: list[tuple], top_k: int
) -> list[tuple]:
    """Reorder hybrid ``candidates`` ([(score, filename, text)]) with an external
    cross-encoder reranker and keep the top_k. Uses its own client (operator-
    configured endpoint, like the embedding/upstream URLs) and FALLS BACK to the
    hybrid order on any failure — reranking only ever sharpens, never breaks, RAG."""
    docs = [txt for _s, _fn, txt in candidates]
    payload = {
        "model": settings.rerank_model,
        "query": query,
        "documents": docs,
        "top_n": top_k,
    }
    headers = {}
    if settings.rerank_api_key:
        headers["authorization"] = f"Bearer {settings.rerank_api_key}"
    try:
        async with httpx.AsyncClient(timeout=settings.rerank_timeout_seconds) as c:
            r = await c.post(settings.rerank_url, json=payload, headers=headers)
        if r.status_code >= 400:
            return candidates[:top_k]
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return candidates[:top_k]
    # Cohere/Jina wrap in {"results": [...]}; TEI/Infinity return a bare list.
    results = data.get("results") if isinstance(data, dict) else data
    if not isinstance(results, list):
        return candidates[:top_k]
    scored: list[tuple[float, int]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        score = item.get("relevance_score", item.get("score"))
        if isinstance(idx, int) and 0 <= idx < len(candidates) and isinstance(score, (int, float)):
            scored.append((float(score), idx))
    if not scored:
        return candidates[:top_k]
    scored.sort(key=lambda x: x[0], reverse=True)  # robust to unsorted responses
    out: list[tuple] = []
    seen: set[int] = set()
    for _s, i in scored:  # dedupe indices defensively
        if i in seen:
            continue
        seen.add(i)
        out.append(candidates[i])
        if len(out) >= top_k:
            break
    return out


# ---------- retrieval ----------

async def _legacy_doc_text(db, table: str, fk: str, doc_id: int) -> str:
    """Reassemble a pre-full_text document by joining its chunks. Chunks overlap,
    so this repeats some boundary text — only used for documents ingested before
    full_text was stored; new uploads keep the exact text."""
    cur = await db.execute(
        f"SELECT text FROM {table} WHERE {fk} = ? ORDER BY seq", (doc_id,)
    )
    return "\n".join(r[0] for r in await cur.fetchall())


async def _full_document_context(
    db: aiosqlite.Connection, cid: str
) -> tuple[str | None, list[dict]]:
    """Full-context RAG: inject each attached document verbatim (the stored
    full_text) instead of top-k retrieval — best for small docs / high fidelity.
    Bounded by a char budget derived from max_context_tokens."""
    docs: list[tuple[str, str]] = []  # (filename, full_text)

    # The conversation's own uploads...
    cur = await db.execute(
        "SELECT id, filename, full_text FROM documents WHERE conversation_id = ? ORDER BY id",
        (cid,),
    )
    for doc_id, filename, full in await cur.fetchall():
        text = full if full is not None else await _legacy_doc_text(db, "chunks", "document_id", doc_id)
        if text:
            docs.append((filename, text))

    # ...plus every attached knowledge-base collection.
    cur = await db.execute(
        """
        SELECT cd.id, cd.filename, cd.full_text
        FROM collection_documents cd
        WHERE cd.collection_id IN (
            SELECT collection_id FROM conversation_collections WHERE conversation_id = ?
        )
        ORDER BY cd.collection_id, cd.id
        """,
        (cid,),
    )
    for doc_id, filename, full in await cur.fetchall():
        text = (
            full if full is not None
            else await _legacy_doc_text(db, "collection_chunks", "document_id", doc_id)
        )
        if text:
            docs.append((filename, text))

    if not docs:
        return None, []

    budget = (settings.max_context_tokens or 30000) * 4  # ~chars (≈4 chars/token)
    sources: list[dict] = []
    sections: list[str] = []
    used = 0
    for i, (fn, full) in enumerate(docs, start=1):
        if used >= budget:
            break
        body = full[: budget - used]
        used += len(body)
        sources.append({"kind": "document", "label": fn, "snippet": snippet(body)})
        sections.append(f'[{i}] from "{fn}":\n{neutralize_markers(body)}')
    context = (
        "The full text of the documents the user attached to this conversation is "
        "below. Use it when answering, and when a statement relies on one, cite it "
        "inline with its bracketed number, e.g. [1]. Say so if the answer isn't "
        "supported by the documents.\n\n" + "\n\n".join(sections)
    )
    return context, sources


async def retrieve_context(
    db: aiosqlite.Connection,
    http: httpx.AsyncClient,
    cid: str,
    query: str,
    top_k: int | None = None,
    full_context: bool = False,
) -> tuple[str | None, list[dict]]:
    """Embed the query, score the conversation's chunks (own uploads + attached
    collections), and return (context_block, sources). sources is a list of
    {kind: "document", label: filename}. (None, []) if nothing matched.

    When full_context is set, skip embedding/ranking and inject whole documents
    verbatim instead (see _full_document_context)."""
    if full_context:
        return await _full_document_context(db, cid)
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

    # When a reranker is configured, pull a wider candidate set first, then let it
    # re-score down to top_k; otherwise the hybrid top_k is used directly.
    rerank_on = bool(settings.rerank_url)
    candidate_k = max(top_k, settings.rag_rerank_candidates) if rerank_on else top_k
    top = _hybrid_rank(query, query_vec, rows, candidate_k, use_bm25=settings.rag_hybrid)
    if not top:
        return None, []
    if rerank_on and len(top) > 1:
        top = await _rerank(query, top, top_k)
    else:
        top = top[:top_k]

    # One numbered source per retrieved excerpt (rank order), each carrying a
    # short snippet for the citation hovercard. The excerpt numbers in the
    # context match the 1-based indices into `sources`, so the model can cite
    # inline as [1], [2], … and the client can resolve each marker to its source.
    sources: list[dict] = []
    sections: list[str] = []
    for i, (_score, fn, txt) in enumerate(top, start=1):
        sources.append({"kind": "document", "label": fn, "snippet": snippet(txt)})
        sections.append(f'[{i}] from "{fn}":\n{neutralize_markers(txt)}')
    context = (
        "Numbered excerpts from documents the user attached to this conversation "
        "are below. Use them when answering, and when a statement relies on one, "
        "cite it inline with its bracketed number, e.g. [1]. Say so if the answer "
        "isn't supported by the excerpts.\n\n" + "\n\n".join(sections)
    )
    return context, sources
