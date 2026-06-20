"""RAG/web citation sources: emitted live (event: sources) and persisted on the
assistant message."""


async def _signup(client):
    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})


async def test_rag_sources_emitted_and_persisted(client):
    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    await client.post(
        f"/api/conversations/{cid}/documents",
        files={"file": ("kb.txt", b"penguins huddle to stay warm", "text/plain")},
    )

    raw = b""
    async with client.stream(
        "POST", f"/api/conversations/{cid}/messages", json={"content": "penguins"}
    ) as r:
        assert r.status_code == 200
        async for chunk in r.aiter_bytes():
            raw += chunk
    assert "event: sources" in raw.decode()

    conv = (await client.get(f"/api/conversations/{cid}")).json()
    asst = [m for m in conv["messages"] if m["role"] == "assistant"][-1]
    assert asst["sources"]
    src = asst["sources"][0]
    assert src["kind"] == "document" and src["label"] == "kb.txt"


async def test_rag_sources_have_snippet_and_numbered_context(client, upstream):
    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    await client.post(
        f"/api/conversations/{cid}/documents",
        files={"file": ("kb.txt", b"penguins huddle together to stay warm", "text/plain")},
    )
    async with client.stream(
        "POST", f"/api/conversations/{cid}/messages", json={"content": "penguins"}
    ) as r:
        async for _ in r.aiter_bytes():
            pass

    # the injected system context numbers each excerpt and asks for inline [n].
    sys_blob = "\n".join(
        m["content"] for m in upstream.chat_calls[-1]["messages"] if m["role"] == "system"
    )
    assert '[1] from "kb.txt"' in sys_blob
    assert "cite it inline" in sys_blob.lower()

    # the persisted source carries a snippet for the citation hovercard.
    conv = (await client.get(f"/api/conversations/{cid}")).json()
    asst = [m for m in conv["messages"] if m["role"] == "assistant"][-1]
    assert asst["sources"][0]["snippet"]
    assert "penguins" in asst["sources"][0]["snippet"]


async def test_shared_conversation_includes_sources(client):
    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    await client.post(
        f"/api/conversations/{cid}/documents",
        files={"file": ("kb.txt", b"penguins huddle to stay warm", "text/plain")},
    )
    async with client.stream(
        "POST", f"/api/conversations/{cid}/messages", json={"content": "penguins"}
    ) as r:
        async for _ in r.aiter_bytes():
            pass
    token = (await client.post(f"/api/conversations/{cid}/share")).json()["token"]
    shared = (await client.get(f"/api/shared/{token}")).json()
    asst = [m for m in shared["messages"] if m["role"] == "assistant"][-1]
    assert asst.get("sources")
    assert asst["sources"][0]["label"] == "kb.txt"


async def test_sources_not_emitted_on_upstream_error(client, upstream):
    from tests.conftest import error_response

    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    await client.post(
        f"/api/conversations/{cid}/documents",
        files={"file": ("kb.txt", b"penguins huddle", "text/plain")},
    )
    upstream.queue_chat(error_response(500, "boom"))
    raw = b""
    async with client.stream(
        "POST", f"/api/conversations/{cid}/messages", json={"content": "penguins"}
    ) as r:
        async for chunk in r.aiter_bytes():
            raw += chunk
    text = raw.decode()
    assert '"error"' in text
    assert "event: sources" not in text  # deferred until the upstream actually streams


async def test_no_sources_without_rag(client):
    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    async with client.stream(
        "POST", f"/api/conversations/{cid}/messages", json={"content": "hi"}
    ) as r:
        raw = b""
        async for chunk in r.aiter_bytes():
            raw += chunk
    assert "event: sources" not in raw.decode()

    conv = (await client.get(f"/api/conversations/{cid}")).json()
    asst = [m for m in conv["messages"] if m["role"] == "assistant"][-1]
    assert asst["sources"] is None
