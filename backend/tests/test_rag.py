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
