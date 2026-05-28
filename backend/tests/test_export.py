import json


async def _signup(client):
    await client.post(
        "/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"}
    )


async def _consume(client, path, json_body):
    async with client.stream("POST", path, json=json_body) as r:
        async for _ in r.aiter_lines():
            pass


async def test_export_json_and_markdown(client):
    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    await _consume(
        client, f"/api/conversations/{cid}/messages", {"content": "Hello world"}
    )

    j = await client.get(f"/api/conversations/{cid}/export?format=json")
    assert j.status_code == 200
    assert j.headers["content-type"].startswith("application/json")
    assert 'filename="Hello-world' in j.headers["content-disposition"]
    payload = json.loads(j.content)
    assert payload["messages"][0]["content"] == "Hello world"

    m = await client.get(f"/api/conversations/{cid}/export?format=md")
    assert m.status_code == 200
    body = m.content.decode()
    assert "# Hello world" in body
    assert "## user" in body
    assert "## assistant" in body
    assert "echo: Hello world" in body


async def test_export_other_user_forbidden(client):
    """Export must respect conversation ownership."""
    import time as _t
    from app.auth import hash_password
    from app.main import app

    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    await _consume(
        client, f"/api/conversations/{cid}/messages", {"content": "owned"}
    )
    await app.state.db.execute(
        "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
        ("bob", hash_password("hunter22hunter"), "user", int(_t.time())),
    )
    await app.state.db.commit()
    await client.post("/api/auth/logout")
    await client.post(
        "/api/auth/login", json={"username": "bob", "password": "hunter22hunter"}
    )
    assert (
        await client.get(f"/api/conversations/{cid}/export?format=json")
    ).status_code == 404
