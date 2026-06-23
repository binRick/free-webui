"""Full-context RAG mode: inject whole attached documents verbatim instead of
top-k retrieval."""


async def _signup(client):
    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})


def _sys_blob(upstream) -> str:
    return "\n".join(
        m["content"] for m in upstream.chat_calls[-1]["messages"] if m["role"] == "system"
    )


async def test_full_context_injects_whole_document(client, upstream):
    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    # long enough to span many chunks; a unique sentinel sits in the LAST chunk
    body = ("alpha beta gamma delta. " * 400 + "ZEBRA_SENTINEL_AT_END").encode()
    await client.post(
        f"/api/conversations/{cid}/documents",
        files={"file": ("big.txt", body, "text/plain")},
    )

    r = await client.patch(f"/api/conversations/{cid}", json={"full_context": True})
    assert r.status_code == 200 and r.json()["full_context"] is True

    async with client.stream(
        "POST", f"/api/conversations/{cid}/messages", json={"content": "unrelated query"}
    ) as resp:
        async for _ in resp.aiter_bytes():
            pass

    blob = _sys_blob(upstream)
    assert "The full text of the documents" in blob  # full-context preamble
    assert '[1] from "big.txt"' in blob
    # the tail of the document is present — proof it's whole-doc, not top-k
    assert "ZEBRA_SENTINEL_AT_END" in blob


async def test_full_context_off_uses_topk_retrieval(client, upstream):
    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    await client.post(
        f"/api/conversations/{cid}/documents",
        files={"file": ("kb.txt", b"penguins huddle to stay warm", "text/plain")},
    )
    async with client.stream(
        "POST", f"/api/conversations/{cid}/messages", json={"content": "penguins"}
    ) as resp:
        async for _ in resp.aiter_bytes():
            pass

    blob = _sys_blob(upstream)
    assert "Numbered excerpts from documents" in blob  # retrieval preamble
    assert "The full text of the documents" not in blob


async def test_full_context_is_exact_on_repetitive_text(client, upstream):
    # Repetitive content is where naive chunk-stitching loses data; the stored
    # full_text makes the injection exact — every occurrence survives.
    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    body = ("REPEAT_BLOCK " * 500).encode()  # ~6500 chars, well under budget
    await client.post(
        f"/api/conversations/{cid}/documents",
        files={"file": ("rep.txt", body, "text/plain")},
    )
    await client.patch(f"/api/conversations/{cid}", json={"full_context": True})
    async with client.stream(
        "POST", f"/api/conversations/{cid}/messages", json={"content": "hi"}
    ) as resp:
        async for _ in resp.aiter_bytes():
            pass
    blob = _sys_blob(upstream)
    assert blob.count("REPEAT_BLOCK") == 500  # no content collapsed/lost


async def test_full_context_flag_persists(client):
    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    assert (await client.get(f"/api/conversations/{cid}")).json()["full_context"] is False
    await client.patch(f"/api/conversations/{cid}", json={"full_context": True})
    assert (await client.get(f"/api/conversations/{cid}")).json()["full_context"] is True
    await client.patch(f"/api/conversations/{cid}", json={"full_context": False})
    assert (await client.get(f"/api/conversations/{cid}")).json()["full_context"] is False
