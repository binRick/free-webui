"""Object/media store: base64 image payloads are externalized out of message
content into the `files` table, served via /api/files/{id}, and re-inlined for
upstream vision replay and public share rendering."""
import base64
import json

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes" * 4
PNG_B64 = base64.b64encode(PNG).decode("ascii")
DATA_URL = f"data:image/png;base64,{PNG_B64}"


async def _admin(client):
    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})


async def _make_bob(client) -> int:
    return (
        await client.post(
            "/api/admin/users", json={"username": "bob", "password": "passpass", "role": "user"}
        )
    ).json()["id"]


async def _login(client, username, password="passpass"):
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": username, "password": password})


async def _consume(client, method, path, body):
    async with client.stream(method, path, json=body) as r:
        assert r.status_code == 200, await r.aread()
        async for _ in r.aiter_lines():
            pass


async def _image_ref(client, cid):
    """The /api/files ref of the first user message's image (content is stored
    as a JSON-encoded part array string, decoded client-side)."""
    msgs = (await client.get(f"/api/conversations/{cid}")).json()["messages"]
    parts = json.loads(msgs[0]["content"])
    return next(p["image_url"]["url"] for p in parts if p["type"] == "image_url")


async def _send_image(client, cid, text="look at this"):
    await _consume(
        client,
        "POST",
        f"/api/conversations/{cid}/messages",
        {
            "content": [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": DATA_URL}},
            ]
        },
    )


# ---- unit-level helpers ----

def test_store_passthrough_non_data_urls():
    from app.files import _DATA_URL_RE, _REF_RE

    # http URLs and existing refs are not data: URLs
    assert _DATA_URL_RE.match("https://img.test/x.png") is None
    assert _DATA_URL_RE.match("/api/files/abc") is None
    # a non-base64 data URL is matched but has no ;base64 group -> left inline
    m = _DATA_URL_RE.match("data:text/plain,hello")
    assert m is not None and m.group(2) is None
    assert _REF_RE.match("/api/files/AbC-123_xyz")
    assert _REF_RE.match("/api/files/../etc") is None


# ---- user-uploaded image: externalize + serve + vision replay ----

async def test_user_image_externalized_and_served(client, upstream):
    await _admin(client)
    cid = (await client.post("/api/conversations", json={"model": None})).json()["id"]
    await _send_image(client, cid)

    msgs = (await client.get(f"/api/conversations/{cid}")).json()["messages"]
    user_msg = msgs[0]
    assert user_msg["role"] == "user"
    # The big base64 payload is gone from the stored row; a compact ref remains.
    assert "base64" not in user_msg["content"]
    ref = await _image_ref(client, cid)
    assert ref.startswith("/api/files/")

    # The file endpoint serves the original bytes with the right content-type.
    fr = await client.get(ref)
    assert fr.status_code == 200
    assert fr.headers["content-type"].startswith("image/png")
    assert fr.content == PNG


async def test_vision_replayed_to_upstream(client, upstream):
    await _admin(client)
    cid = (await client.post("/api/conversations", json={"model": None})).json()["id"]
    await _send_image(client, cid)
    # A second turn replays history; the stored ref must be re-inlined as a
    # data: URL so the upstream vision model actually receives the image.
    await _consume(client, "POST", f"/api/conversations/{cid}/messages", {"content": "and now?"})

    last = upstream.chat_calls[-1]
    replayed = next(m for m in last["messages"] if isinstance(m.get("content"), list))
    img = next(p for p in replayed["content"] if p["type"] == "image_url")
    assert img["image_url"]["url"] == DATA_URL


# ---- ownership / auth ----

async def test_file_requires_auth_and_ownership(client):
    await _admin(client)
    cid = (await client.post("/api/conversations", json={"model": None})).json()["id"]
    await _send_image(client, cid)
    ref = await _image_ref(client, cid)

    # unauthenticated -> 401
    await client.post("/api/auth/logout")
    assert (await client.get(ref)).status_code == 401

    # a different (non-admin) user cannot read someone else's file -> 404
    await client.post("/api/auth/login", json={"username": "alice", "password": "hunter22hunter"})
    await _make_bob(client)
    await _login(client, "bob")
    assert (await client.get(ref)).status_code == 404

    # missing id -> 404
    assert (await client.get("/api/files/does-not-exist")).status_code == 404


async def test_admin_can_read_any_file(client):
    await _admin(client)
    cid = (await client.post("/api/conversations", json={"model": None})).json()["id"]
    await _send_image(client, cid)
    ref = await _image_ref(client, cid)
    # alice (the setup user) is admin and owns it; the admin branch is exercised
    # alongside ownership.
    assert (await client.get(ref)).status_code == 200


# ---- public share inlines image bytes ----

async def test_truncate_and_conv_delete_reclaim_blobs(client):
    await _admin(client)
    cid = (await client.post("/api/conversations", json={"model": None})).json()["id"]
    await _send_image(client, cid)
    ref = await _image_ref(client, cid)
    assert (await client.get(ref)).status_code == 200

    # delete the message that referenced it -> blob reclaimed (gc_orphan_files)
    msg_id = (await client.get(f"/api/conversations/{cid}")).json()["messages"][0]["id"]
    await client.delete(f"/api/conversations/{cid}/messages/{msg_id}")
    assert (await client.get(ref)).status_code == 404

    # and deleting the whole conversation reclaims its blobs (FK CASCADE)
    cid2 = (await client.post("/api/conversations", json={"model": None})).json()["id"]
    await _send_image(client, cid2)
    ref2 = await _image_ref(client, cid2)
    assert (await client.get(ref2)).status_code == 200
    await client.delete(f"/api/conversations/{cid2}")
    assert (await client.get(ref2)).status_code == 404


async def test_clone_copies_blobs_and_is_decoupled(client):
    await _admin(client)
    cid = (await client.post("/api/conversations", json={"model": None})).json()["id"]
    await _send_image(client, cid)
    orig_ref = await _image_ref(client, cid)

    new_id = (await client.post(f"/api/conversations/{cid}/clone")).json()["id"]
    clone_parts = json.loads(
        (await client.get(f"/api/conversations/{new_id}")).json()["messages"][0]["content"]
    )
    clone_ref = next(p["image_url"]["url"] for p in clone_parts if p["type"] == "image_url")
    # the clone got its own blob (distinct id) serving the same bytes
    assert clone_ref != orig_ref
    assert (await client.get(clone_ref)).content == PNG

    # deleting the original must not break the clone's image
    await client.delete(f"/api/conversations/{cid}")
    assert (await client.get(orig_ref)).status_code == 404
    assert (await client.get(clone_ref)).status_code == 200


async def test_forged_cross_conversation_ref_not_inlined(client, monkeypatch):
    """A user must not be able to embed a forged /api/files ref pointing at a
    file from another conversation and exfiltrate its bytes through the public
    share path (expand_file_refs is scoped to the conversation)."""
    from app.config import settings

    monkeypatch.setattr(settings, "allow_public_sharing", True)
    await _admin(client)

    # Conversation A holds a real externalized image -> learn its file id.
    cid_a = (await client.post("/api/conversations", json={"model": None})).json()["id"]
    await _send_image(client, cid_a)
    victim_ref = await _image_ref(client, cid_a)  # /api/files/<id> owned by conv A

    # Conversation B embeds that ref by hand (no data URL -> stored verbatim).
    cid_b = (await client.post("/api/conversations", json={"model": None})).json()["id"]
    await _consume(
        client,
        "POST",
        f"/api/conversations/{cid_b}/messages",
        {"content": [{"type": "image_url", "image_url": {"url": victim_ref}}]},
    )
    token = (await client.post(f"/api/conversations/{cid_b}/share")).json()["token"]

    await client.post("/api/auth/logout")
    shared = (await client.get(f"/api/shared/{token}")).json()
    img = next(p for p in shared["messages"][0]["content"] if p["type"] == "image_url")
    # The ref stays a bare ref (404 for the public viewer) — bytes are NOT inlined.
    assert img["image_url"]["url"] == victim_ref
    assert "base64" not in json.dumps(shared["messages"])


async def test_public_share_inlines_image(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "allow_public_sharing", True)
    await _admin(client)
    cid = (await client.post("/api/conversations", json={"model": None})).json()["id"]
    await _send_image(client, cid)
    token = (await client.post(f"/api/conversations/{cid}/share")).json()["token"]

    # Logged-out public viewer can't hit /api/files; the share payload must
    # carry the image inline as a data: URL instead of a ref.
    await client.post("/api/auth/logout")
    shared = (await client.get(f"/api/shared/{token}")).json()
    parts = shared["messages"][0]["content"]
    img = next(p for p in parts if p["type"] == "image_url")
    assert img["image_url"]["url"] == DATA_URL
