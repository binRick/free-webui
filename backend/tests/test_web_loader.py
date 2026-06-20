"""RAG URL loader: HTML→text extraction, SSRF-guarded fetch, and ingesting a
fetched page/PDF/text into a conversation or collection like an uploaded file."""
import httpx

from app.web_loader import _html_to_text, _label_from_url


async def _signup(client):
    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})


def _serve(monkeypatch, handler):
    """Route every fresh httpx.AsyncClient (web_loader's) through a MockTransport.
    The shared upstream client (embeddings) was built before this and is untouched."""
    orig = httpx.AsyncClient

    class Patched(orig):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            kw.pop("base_url", None)
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", Patched)


# ---- unit: HTML extraction ----

def test_html_to_text_strips_scripts_and_gets_title():
    html = (
        b"<html><head><title>  My  Page  </title><style>.a{color:red}</style>"
        b"</head><body><h1>Heading</h1><p>Hello world.</p>"
        b"<script>alert('x')</script></body></html>"
    )
    title, text = _html_to_text(html)
    assert title == "My Page"
    assert "Heading" in text and "Hello world." in text
    assert "alert" not in text and "color:red" not in text


def test_html_to_text_separates_table_cells():
    html = b"<table><tr><td>Name</td><td>Alice</td></tr><tr><td>Age</td><td>30</td></tr></table>"
    _, text = _html_to_text(html)
    assert "NameAlice" not in text and "Age30" not in text
    assert "Name" in text and "Alice" in text and "30" in text


def test_html_to_text_first_title_only_no_leak():
    html = (
        b"<html><head><title>Real Title</title></head><body><p>Body.</p>"
        b"<svg><title>icon</title></svg><title>Stray</title></body></html>"
    )
    title, text = _html_to_text(html)
    assert title == "Real Title"
    assert "Stray" not in text and "icon" not in text


def test_html_to_text_strips_nested_markup_in_title():
    html = b"<html><head><title>A <b>B</b> C</title></head><body><p>x</p></body></html>"
    title, _ = _html_to_text(html)
    assert title == "A B C"


def test_label_from_url():
    assert _label_from_url("https://ex.com/docs/guide.html") == "guide.html"
    assert _label_from_url("https://ex.com/") == "ex.com"
    assert _label_from_url("https://ex.com") == "ex.com"


# ---- integration: ingest ----

async def test_ingest_url_into_conversation(client, monkeypatch):
    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    html = (
        b"<html><head><title>Mars Facts</title></head><body>"
        b"<p>The capital of Mars is Olympus.</p><script>evil()</script></body></html>"
    )
    _serve(monkeypatch, lambda req: httpx.Response(200, content=html, headers={"content-type": "text/html; charset=utf-8"}))

    r = await client.post(
        f"/api/conversations/{cid}/documents/url", json={"url": "http://example.com/mars"}
    )
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["filename"] == "Mars Facts"
    assert doc["mime"] == "text/html"
    assert doc["chunk_count"] >= 1
    listed = (await client.get(f"/api/conversations/{cid}/documents")).json()
    assert any(d["id"] == doc["id"] for d in listed)


async def test_ingest_plain_text_url(client, monkeypatch):
    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    _serve(monkeypatch, lambda req: httpx.Response(
        200, content=b"Plain notes: Jupiter has 95 moons.", headers={"content-type": "text/plain"}
    ))
    r = await client.post(
        f"/api/conversations/{cid}/documents/url", json={"url": "http://example.com/notes.txt"}
    )
    assert r.status_code == 200
    assert r.json()["filename"] == "notes.txt"
    assert r.json()["chunk_count"] >= 1


async def test_ingest_html_mislabeled_as_plain_text_is_sniffed(client, monkeypatch):
    # a page served with the wrong content-type must still be reduced to text
    # (not stored as raw <script>/markup). HTML path -> filename is the <title>.
    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    html = (
        b"<html><head><title>Sniffed</title></head><body>"
        b"<p>Real content here.</p><script>bad()</script></body></html>"
    )
    _serve(monkeypatch, lambda req: httpx.Response(200, content=html, headers={"content-type": "text/plain"}))
    r = await client.post(
        f"/api/conversations/{cid}/documents/url", json={"url": "http://example.com/page"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["filename"] == "Sniffed"  # routed through HTML extraction
    assert r.json()["mime"] == "text/html"


async def test_ingest_url_follows_redirect(client, monkeypatch):
    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]

    def handler(req):
        if req.url.path == "/start":
            return httpx.Response(302, headers={"location": "http://example.com/final"})
        return httpx.Response(
            200,
            content=b"<html><title>Final</title><body><p>Arrived here.</p></body></html>",
            headers={"content-type": "text/html"},
        )

    _serve(monkeypatch, handler)
    r = await client.post(
        f"/api/conversations/{cid}/documents/url", json={"url": "http://example.com/start"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["filename"] == "Final"


async def test_ingest_url_into_collection(client, monkeypatch):
    await _signup(client)
    coll = (await client.post("/api/collections", json={"name": "web"})).json()
    _serve(monkeypatch, lambda req: httpx.Response(
        200,
        content=b"<html><title>KB Doc</title><body><p>Saturn ring facts.</p></body></html>",
        headers={"content-type": "text/html"},
    ))
    r = await client.post(
        f"/api/collections/{coll['id']}/documents/url", json={"url": "http://example.com/kb"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["filename"] == "KB Doc"
    docs = (await client.get(f"/api/collections/{coll['id']}/documents")).json()
    assert len(docs) == 1


# ---- guards ----

async def test_ingest_url_too_large(client, monkeypatch):
    from app.config import settings

    settings.rag_max_upload_bytes = 50
    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    big = b"<html><body>" + b"x" * 5000 + b"</body></html>"
    _serve(monkeypatch, lambda req: httpx.Response(200, content=big, headers={"content-type": "text/html"}))
    r = await client.post(
        f"/api/conversations/{cid}/documents/url", json={"url": "http://example.com/big"}
    )
    assert r.status_code == 413


async def test_ingest_url_ssrf_blocked(client):
    from app.config import settings

    settings.ssrf_protection = True
    settings.ssrf_block_private = True
    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    # a literal loopback address is refused by netguard before any fetch.
    r = await client.post(
        f"/api/conversations/{cid}/documents/url", json={"url": "http://127.0.0.1/secret"}
    )
    assert r.status_code == 400


async def test_ingest_url_http_error(client, monkeypatch):
    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    _serve(monkeypatch, lambda req: httpx.Response(404, content=b"nope"))
    r = await client.post(
        f"/api/conversations/{cid}/documents/url", json={"url": "http://example.com/missing"}
    )
    assert r.status_code == 502


async def test_ingest_url_permission_gate(client):
    await _signup(client)  # alice = admin
    await client.post(
        "/api/admin/users", json={"username": "bob", "password": "passpass", "role": "user"}
    )
    await client.put("/api/admin/permissions/defaults", json={"defaults": {"file_upload": False}})
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": "bob", "password": "passpass"})
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    # denied before any fetch is attempted
    r = await client.post(
        f"/api/conversations/{cid}/documents/url", json={"url": "http://example.com/x"}
    )
    assert r.status_code == 403
