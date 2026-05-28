async def _signup(client):
    await client.post(
        "/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"}
    )


async def test_mint_then_use_for_v1(client):
    await _signup(client)
    minted = await client.post("/api/api_keys", json={"name": "cli"})
    assert minted.status_code == 200
    key = minted.json()["key"]
    assert key.startswith("fw_")
    assert minted.json()["key_prefix"].startswith("fw_")

    # /v1 endpoints reject missing / wrong tokens
    assert (await client.get("/v1/models")).status_code == 401
    bad = await client.get("/v1/models", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401

    good = await client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert good.status_code == 200
    assert "data" in good.json()


async def test_revoke_then_blocked(client):
    await _signup(client)
    minted = await client.post("/api/api_keys", json={"name": "ephemeral"})
    key = minted.json()["key"]
    kid = minted.json()["id"]
    assert (
        await client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    ).status_code == 200
    assert (await client.delete(f"/api/api_keys/{kid}")).status_code == 204
    assert (
        await client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    ).status_code == 401


async def test_v1_chat_completions_stream(client):
    await _signup(client)
    key = (await client.post("/api/api_keys", json={"name": "k"})).json()["key"]
    deltas: list[str] = []
    async with client.stream(
        "POST",
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": "fake-a",
            "messages": [{"role": "user", "content": "ping"}],
            "stream": True,
        },
    ) as r:
        assert r.status_code == 200
        async for line in r.aiter_lines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            import json as _json
            chunk = _json.loads(data)
            d = chunk.get("choices", [{}])[0].get("delta", {}).get("content")
            if isinstance(d, str):
                deltas.append(d)
    assert "".join(deltas).strip() == "echo: ping"
