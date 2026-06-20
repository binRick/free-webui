"""Fine-grained per-feature permission matrix: the admin API, effective-permission
resolution (defaults + group grants), and enforcement at every gated surface
(tool composition, uploads, knowledge, notes, temporary chat, share links)."""
from tests.conftest import content_chunk, finish, sse


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


async def _set_default(client, key, allowed):
    return await client.put("/api/admin/permissions/defaults", json={"defaults": {key: allowed}})


# ---- resolution ----

async def test_me_defaults_all_allowed(client):
    await _admin(client)
    await _make_bob(client)
    await _login(client, "bob")
    perms = (await client.get("/api/permissions/me")).json()
    assert perms["web_search"] is True
    assert perms["image_generation"] is True
    assert set(perms) >= {"web_search", "file_upload", "knowledge", "notes", "tools"}


async def test_admin_bypasses_matrix(client):
    await _admin(client)
    await _set_default(client, "web_search", False)
    await _set_default(client, "notes", False)
    perms = (await client.get("/api/permissions/me")).json()
    assert all(perms.values())  # admin is always permitted


async def test_default_off_denies_regular_user(client):
    await _admin(client)
    await _make_bob(client)
    await _set_default(client, "web_search", False)
    await _login(client, "bob")
    assert (await client.get("/api/permissions/me")).json()["web_search"] is False


async def test_group_grant_widens_above_default(client):
    await _admin(client)
    bob = await _make_bob(client)
    await _set_default(client, "image_generation", False)
    g = (await client.post("/api/admin/groups", json={"name": "creators"})).json()
    await client.put(f"/api/admin/groups/{g['id']}/members", json={"user_ids": [bob]})
    await client.put(
        f"/api/admin/permissions/groups/{g['id']}", json={"keys": ["image_generation"]}
    )

    await _login(client, "bob")
    perms = (await client.get("/api/permissions/me")).json()
    assert perms["image_generation"] is True  # group grant overrides default-off
    assert perms["code_interpreter"] is True  # untouched default


async def test_group_grant_only_for_members(client):
    await _admin(client)
    await _make_bob(client)
    await _set_default(client, "image_generation", False)
    g = (await client.post("/api/admin/groups", json={"name": "creators"})).json()
    await client.put(
        f"/api/admin/permissions/groups/{g['id']}", json={"keys": ["image_generation"]}
    )
    # bob is NOT a member -> still denied
    await _login(client, "bob")
    assert (await client.get("/api/permissions/me")).json()["image_generation"] is False


# ---- admin API surface ----

async def test_matrix_shape(client):
    await _admin(client)
    g = (await client.post("/api/admin/groups", json={"name": "team"})).json()
    await client.put(f"/api/admin/permissions/groups/{g['id']}", json={"keys": ["notes", "bogus"]})
    matrix = (await client.get("/api/admin/permissions")).json()
    keys = {p["key"] for p in matrix["permissions"]}
    assert "web_search" in keys and "chat_share" in keys
    assert matrix["defaults"]["web_search"] is True
    grp = next(x for x in matrix["groups"] if x["id"] == g["id"])
    assert grp["keys"] == ["notes"]  # unknown key dropped


async def test_set_defaults_unknown_key(client):
    await _admin(client)
    # a mix keeps known keys, ignores unknown
    await client.put(
        "/api/admin/permissions/defaults", json={"defaults": {"notes": False, "nope": True}}
    )
    defaults = (await client.get("/api/admin/permissions")).json()["defaults"]
    assert defaults["notes"] is False and "nope" not in defaults
    # only-unknown is a 422
    r = await client.put("/api/admin/permissions/defaults", json={"defaults": {"nope": True}})
    assert r.status_code == 422


async def test_group_perms_unknown_group_404(client):
    await _admin(client)
    r = await client.put("/api/admin/permissions/groups/9999", json={"keys": ["notes"]})
    assert r.status_code == 404


async def test_admin_endpoints_require_admin(client):
    await _admin(client)
    await _make_bob(client)
    await _login(client, "bob")
    assert (await client.get("/api/admin/permissions")).status_code == 403
    assert (
        await client.put("/api/admin/permissions/defaults", json={"defaults": {"notes": False}})
    ).status_code == 403


# ---- enforcement: endpoint gates ----

async def test_file_upload_gate(client):
    await _admin(client)
    await _make_bob(client)
    await _set_default(client, "file_upload", False)
    await _login(client, "bob")
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    r = await client.post(
        f"/api/conversations/{cid}/documents",
        files={"file": ("a.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 403


async def test_knowledge_gate(client):
    await _admin(client)
    await _make_bob(client)
    await _set_default(client, "knowledge", False)
    await _login(client, "bob")
    assert (await client.post("/api/collections", json={"name": "kb"})).status_code == 403


async def test_notes_gate(client):
    await _admin(client)
    await _make_bob(client)
    await _set_default(client, "notes", False)
    await _login(client, "bob")
    assert (await client.post("/api/notes", json={"title": "x"})).status_code == 403


async def test_temporary_chat_gate(client):
    await _admin(client)
    await _make_bob(client)
    await _set_default(client, "temporary_chat", False)
    await _login(client, "bob")
    r = await client.post(
        "/api/chat/temporary",
        json={"messages": [{"role": "user", "content": "hi"}], "model": "fake-a"},
    )
    assert r.status_code == 403


async def test_share_gate(client):
    await _admin(client)
    await _make_bob(client)
    await _set_default(client, "chat_share", False)
    await _login(client, "bob")
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    assert (await client.post(f"/api/conversations/{cid}/share")).status_code == 403


async def test_notes_update_gate_but_delete_allowed(client):
    # the notes gate covers create + edit (content modification); deleting an
    # existing note stays allowed so a restricted user can still clean up.
    await _admin(client)
    await _make_bob(client)
    await _login(client, "bob")
    nid = (await client.post("/api/notes", json={"title": "keep"})).json()["id"]

    await _login(client, "alice", "hunter22hunter")
    await _set_default(client, "notes", False)

    await _login(client, "bob")
    assert (await client.patch(f"/api/notes/{nid}", json={"title": "edited"})).status_code == 403
    assert (await client.delete(f"/api/notes/{nid}")).status_code == 204


def test_require_permission_rejects_unknown_key():
    import pytest

    from app.permissions import require_permission

    require_permission("web_search")  # known key is fine
    with pytest.raises(ValueError):
        require_permission("definitely_not_a_real_key")


async def test_admin_not_blocked_by_gates(client):
    # an admin with every default off can still use the features.
    await _admin(client)
    for key in ("file_upload", "knowledge", "notes", "chat_share"):
        await _set_default(client, key, False)
    assert (await client.post("/api/collections", json={"name": "kb"})).status_code == 200
    assert (await client.post("/api/notes", json={"title": "x"})).status_code == 200
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    assert (await client.post(f"/api/conversations/{cid}/share")).status_code == 200


# ---- enforcement: tool composition ----

async def _tool_names_for_send(client, upstream, cid):
    """Send one message and return the tool names offered to the upstream."""
    upstream.queue_chat(sse(content_chunk("ok"), finish()))
    before = len(upstream.chat_calls)
    async with client.stream(
        "POST", f"/api/conversations/{cid}/messages",
        json={"content": "hi", "model": "fake-a"},
    ) as r:
        assert r.status_code == 200
        async for _ in r.aiter_bytes():
            pass
    payload = upstream.chat_calls[before]
    return [t["function"]["name"] for t in payload.get("tools", [])]


async def test_tool_specs_gated_by_permission(client, upstream):
    from app.config import settings

    settings.image_backend = "openai"        # makes `imagine` available
    settings.code_interpreter = "subprocess"  # makes `run_python` available

    await _admin(client)
    await _make_bob(client)
    await _login(client, "bob")
    cid = (await client.post("/api/conversations", json={"model": "fake-a"})).json()["id"]
    await client.patch(f"/api/conversations/{cid}", json={"tools_enabled": True})

    names = await _tool_names_for_send(client, upstream, cid)
    assert "imagine" in names and "run_python" in names  # default: allowed

    # admin turns both off
    await _login(client, "alice", "hunter22hunter")
    await _set_default(client, "image_generation", False)
    await _set_default(client, "code_interpreter", False)

    await _login(client, "bob")
    names = await _tool_names_for_send(client, upstream, cid)
    assert "imagine" not in names and "run_python" not in names
    assert "now" in names  # ungated built-ins still offered

    # admin (bypass) still gets them
    await _login(client, "alice", "hunter22hunter")
    acid = (await client.post("/api/conversations", json={"model": "fake-a"})).json()["id"]
    await client.patch(f"/api/conversations/{acid}", json={"tools_enabled": True})
    names = await _tool_names_for_send(client, upstream, acid)
    assert "imagine" in names and "run_python" in names
