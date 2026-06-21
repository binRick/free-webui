"""Tests for the RAG upload + retrieval flow.

The conftest stub returns deterministic toy embeddings (8 floats derived from
text length % 7), so retrieval has a stable ranking and we can assert that
the retrieved excerpts get prepended to the upstream payload.
"""
import json
import math


def test_rank_chunks_orders_by_cosine_and_truncates():
    from app.rag import _rank_chunks, cosine, pack

    q = [1.0, 0.0, 0.0]
    rows = [
        ("near", pack([0.9, 0.1, 0.0]), "a.txt"),
        ("orthogonal", pack([0.0, 1.0, 0.0]), "b.txt"),
        ("aligned", pack([2.0, 0.0, 0.0]), "c.txt"),  # same direction as q -> best
    ]
    ranked = _rank_chunks(q, rows, top_k=2)
    assert [fn for _s, fn, _t in ranked] == ["c.txt", "a.txt"]  # top-2 by cosine
    # scores match the reference cosine implementation
    assert math.isclose(ranked[0][0], cosine(q, [2.0, 0.0, 0.0]), rel_tol=1e-5)
    assert math.isclose(ranked[1][0], cosine(q, [0.9, 0.1, 0.0]), rel_tol=1e-5)


def test_rank_chunks_skips_dimension_mismatch():
    from app.rag import _rank_chunks, pack

    q = [1.0, 0.0]
    rows = [
        ("wrong-dim", pack([1.0, 0.0, 0.0]), "x.txt"),  # 3-d vs 2-d query -> skipped
        ("ok", pack([1.0, 0.0]), "y.txt"),
    ]
    ranked = _rank_chunks(q, rows, top_k=5)
    assert [fn for _s, fn, _t in ranked] == ["y.txt"]


def test_bm25_ranks_keyword_overlap_first():
    from app.rag import _bm25_scores

    docs = [
        "the quick brown fox jumps over the lazy dog",
        "penguins huddle together to stay warm in antarctica",
        "a fox is a small carnivorous mammal",
    ]
    scores = _bm25_scores("fox", docs)
    # both fox docs score > 0, the penguin doc scores 0
    assert scores[0] > 0 and scores[2] > 0 and scores[1] == 0.0
    # the shorter fox doc (higher term density) outranks the longer one
    assert scores[2] > scores[0]
    # no query-term overlap -> all zero
    assert _bm25_scores("helicopter", docs) == [0.0, 0.0, 0.0]


def test_hybrid_surfaces_keyword_only_match():
    """A chunk that an embedding misses (orthogonal vector) but that contains the
    exact query term is still retrieved once BM25 is fused in."""
    from app.rag import _hybrid_rank, pack

    q_vec = [1.0, 0.0, 0.0]
    rows = [
        ("semantic neighbour", pack([0.9, 0.1, 0.0]), "vec.txt"),       # strong cosine
        ("error code E1234 means disk full", pack([0.0, 1.0, 0.0]), "kw.txt"),  # cosine 0
    ]
    # dense-only: the orthogonal keyword chunk is invisible
    dense = _hybrid_rank("E1234", q_vec, rows, top_k=5, use_bm25=False)
    assert [fn for _s, fn, _t in dense] == ["vec.txt"]
    # hybrid: the exact-term chunk surfaces alongside the semantic one
    hybrid = _hybrid_rank("E1234", q_vec, rows, top_k=5, use_bm25=True)
    assert set(fn for _s, fn, _t in hybrid) == {"vec.txt", "kw.txt"}


def test_hybrid_returns_empty_when_nothing_matches():
    from app.rag import _hybrid_rank, pack

    rows = [("unrelated text", pack([0.0, 1.0]), "a.txt")]
    # query vec orthogonal (cosine 0) and no shared keyword -> nothing
    assert _hybrid_rank("zzz", [1.0, 0.0], rows, top_k=5, use_bm25=True) == []


async def _signup(client):
    await client.post(
        "/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"}
    )


async def _make_chat(client):
    r = await client.post("/api/conversations", json={})
    return r.json()["id"]


async def test_upload_chunks_and_embeds(client):
    await _signup(client)
    cid = await _make_chat(client)

    body = "Lorem ipsum dolor sit amet. " * 200  # ~5600 chars → multiple chunks
    files = {"file": ("notes.txt", body.encode("utf-8"), "text/plain")}
    r = await client.post(f"/api/conversations/{cid}/documents", files=files)
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["filename"] == "notes.txt"
    assert doc["chunk_count"] >= 2
    assert doc["embedding_model"] == "nomic-embed-text"

    listed = await client.get(f"/api/conversations/{cid}/documents")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


async def test_unsupported_binary_is_rejected(client):
    await _signup(client)
    cid = await _make_chat(client)
    bad = b"\x89PNG\r\n\x1a\n" + b"\xff" * 50  # binary garbage, no decode
    files = {"file": ("img.png", bad, "image/png")}
    r = await client.post(f"/api/conversations/{cid}/documents", files=files)
    assert r.status_code == 415


async def test_delete_document_cascades_chunks(client):
    await _signup(client)
    cid = await _make_chat(client)
    files = {"file": ("notes.txt", b"hello world this is a test", "text/plain")}
    doc = (await client.post(f"/api/conversations/{cid}/documents", files=files)).json()
    assert (await client.delete(
        f"/api/conversations/{cid}/documents/{doc['id']}"
    )).status_code == 204
    assert (await client.get(f"/api/conversations/{cid}/documents")).json() == []


async def test_send_message_injects_retrieved_context(client):
    """When a document is attached, the upstream payload should include a
    system message carrying the retrieved excerpts.

    We swap the upstream MockTransport for a capturing handler so we can
    inspect the exact JSON the backend sent."""
    import httpx
    from app.main import app

    await _signup(client)
    cid = await _make_chat(client)

    body = "the secret password is purple-fox-77.\n" * 30
    files = {"file": ("secret.txt", body.encode("utf-8"), "text/plain")}
    assert (
        await client.post(f"/api/conversations/{cid}/documents", files=files)
    ).status_code == 200

    captured: dict = {}

    async def capturing(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/embeddings"):
            # delegate to the default handler shape
            from tests.conftest import _fake_handler
            return await _fake_handler(request)
        if request.url.path.endswith("/chat/completions"):
            captured["payload"] = json.loads(request.content)
            from tests.conftest import _fake_chat_stream
            return _fake_chat_stream(captured["payload"])
        return httpx.Response(404)

    app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(capturing),
        base_url="http://upstream/v1",
    )

    async with client.stream(
        "POST",
        f"/api/conversations/{cid}/messages",
        json={"content": "what is the secret password?"},
    ) as r:
        async for _ in r.aiter_lines():
            pass

    payload = captured["payload"]
    systems = [m for m in payload["messages"] if m["role"] == "system"]
    assert systems, f"expected a system context message, got: {payload['messages']}"
    joined = "\n".join(s["content"] for s in systems)
    assert "secret.txt" in joined
    assert "purple-fox-77" in joined


def test_snippet_collapses_and_truncates():
    from app.rag import snippet

    assert snippet("a  b\n c") == "a b c"
    s = snippet("x" * 500, limit=100)
    assert len(s) <= 101 and s.endswith("…")
    assert snippet("") == ""


def _patch_rerank(monkeypatch, handler):
    """Route the reranker's fresh httpx.AsyncClient through a MockTransport."""
    import httpx

    orig = httpx.AsyncClient

    class Patched(orig):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", Patched)


async def test_rerank_reorders_by_relevance(monkeypatch):
    import httpx

    from app import rag

    monkeypatch.setattr(rag.settings, "rerank_url", "http://rr.test/rerank")
    monkeypatch.setattr(rag.settings, "rerank_model", "rr")
    candidates = [(0.9, "a.txt", "alpha"), (0.5, "b.txt", "beta"), (0.1, "c.txt", "gamma")]
    # reranker says gamma is most relevant, then alpha (and returns them unsorted)
    _patch_rerank(monkeypatch, lambda req: httpx.Response(200, json={"results": [
        {"index": 0, "relevance_score": 0.4},
        {"index": 2, "relevance_score": 0.95},
        {"index": 1, "relevance_score": 0.1},
    ]}))
    out = await rag._rerank("q", candidates, top_k=2)
    assert [fn for _s, fn, _t in out] == ["c.txt", "a.txt"]


async def test_rerank_accepts_bare_list_and_score_key(monkeypatch):
    import httpx

    from app import rag

    monkeypatch.setattr(rag.settings, "rerank_url", "http://rr.test/rerank")
    candidates = [(0.9, "a.txt", "alpha"), (0.5, "b.txt", "beta")]
    # TEI-style bare array with "score"
    _patch_rerank(monkeypatch, lambda req: httpx.Response(200, json=[
        {"index": 1, "score": 0.9}, {"index": 0, "score": 0.2},
    ]))
    out = await rag._rerank("q", candidates, top_k=2)
    assert [fn for _s, fn, _t in out] == ["b.txt", "a.txt"]


async def test_rerank_falls_back_to_hybrid_order_on_error(monkeypatch):
    import httpx

    from app import rag

    monkeypatch.setattr(rag.settings, "rerank_url", "http://rr.test/rerank")
    candidates = [(0.9, "a.txt", "alpha"), (0.5, "b.txt", "beta"), (0.1, "c.txt", "gamma")]
    _patch_rerank(monkeypatch, lambda req: httpx.Response(500, text="boom"))
    out = await rag._rerank("q", candidates, top_k=2)
    assert [fn for _s, fn, _t in out] == ["a.txt", "b.txt"]  # hybrid order preserved
