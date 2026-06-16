"""Evaluation suite: feedback leaderboard + arena ELO voting."""


async def _signup(client):
    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})


async def _consume(client, path, body):
    async with client.stream("POST", path, json=body) as r:
        assert r.status_code == 200
        async for _ in r.aiter_bytes():
            pass


async def _rate(client, model: str, rating: int):
    """Create a one-turn conversation on ``model`` and rate its reply."""
    cid = (await client.post("/api/conversations", json={"model": model})).json()["id"]
    await _consume(client, f"/api/conversations/{cid}/messages", {"content": "hi"})
    conv = (await client.get(f"/api/conversations/{cid}")).json()
    aid = next(m["id"] for m in conv["messages"] if m["role"] == "assistant")
    await client.put(
        f"/api/conversations/{cid}/messages/{aid}/feedback", json={"rating": rating}
    )


async def test_message_records_its_model(client):
    """A rated assistant message surfaces under the model that produced it —
    proving the per-message model column is written and joined for the
    leaderboard (legacy NULL-model rows would be excluded)."""
    await _signup(client)
    await _rate(client, "alpha", 1)
    rows = (await client.get("/api/evaluations/leaderboard")).json()
    assert [r["model"] for r in rows] == ["alpha"]
    assert rows[0]["up"] == 1


async def test_leaderboard_feedback_aggregation(client):
    await _signup(client)
    await _rate(client, "alpha", 1)
    await _rate(client, "alpha", 1)
    await _rate(client, "beta", -1)

    rows = {r["model"]: r for r in (await client.get("/api/evaluations/leaderboard")).json()}
    assert rows["alpha"]["up"] == 2 and rows["alpha"]["down"] == 0
    assert rows["beta"]["up"] == 0 and rows["beta"]["down"] == 1
    assert rows["alpha"]["feedback_count"] == 2
    # Wilson lower bound: 2/2 positive ranks above 0/1 positive.
    assert rows["alpha"]["rating"] > rows["beta"]["rating"]
    # No arena games yet -> everyone at the base ELO.
    assert rows["alpha"]["elo"] == 1000 and rows["alpha"]["arena_games"] == 0


async def test_arena_vote_moves_elo(client):
    await _signup(client)
    # fake-a beats fake-b three times -> fake-a's ELO rises above fake-b's.
    for _ in range(3):
        r = await client.post(
            "/api/evaluations/arena/vote",
            json={"model_a": "fake-a", "model_b": "fake-b", "winner": "a", "prompt": "2+2?"},
        )
        assert r.status_code == 201

    rows = {r["model"]: r for r in (await client.get("/api/evaluations/leaderboard")).json()}
    assert rows["fake-a"]["elo"] > 1000 > rows["fake-b"]["elo"]
    assert rows["fake-a"]["wins"] == 3 and rows["fake-a"]["losses"] == 0
    assert rows["fake-b"]["losses"] == 3
    assert rows["fake-a"]["arena_games"] == 3
    # fake-a ranks first (higher ELO).
    ordered = [r["model"] for r in (await client.get("/api/evaluations/leaderboard")).json()]
    assert ordered[0] == "fake-a"


async def test_arena_tie_and_both_bad(client):
    await _signup(client)
    assert (await client.post(
        "/api/evaluations/arena/vote",
        json={"model_a": "fake-a", "model_b": "fake-b", "winner": "tie"},
    )).status_code == 201
    assert (await client.post(
        "/api/evaluations/arena/vote",
        json={"model_a": "fake-a", "model_b": "fake-b", "winner": "both_bad"},
    )).status_code == 201

    rows = {r["model"]: r for r in (await client.get("/api/evaluations/leaderboard")).json()}
    # A tie leaves equal ELO; both_bad moves none. Both games counted.
    assert rows["fake-a"]["elo"] == 1000 and rows["fake-b"]["elo"] == 1000
    assert rows["fake-a"]["arena_games"] == 2 and rows["fake-a"]["ties"] == 2


async def test_arena_vote_validation(client):
    await _signup(client)
    # winner/same-model validation runs BEFORE the availability check, so these
    # 422 regardless of whether the model names exist.
    assert (await client.post(
        "/api/evaluations/arena/vote",
        json={"model_a": "fake-a", "model_b": "fake-a", "winner": "a"},
    )).status_code == 422  # same model
    assert (await client.post(
        "/api/evaluations/arena/vote",
        json={"model_a": "fake-a", "model_b": "fake-b", "winner": "nonsense"},
    )).status_code == 422  # bad winner


async def test_arena_vote_rejects_phantom_model(client):
    """A model not in the resolved available set can't be voted on — this stops
    the leaderboard being stuffed with entries for models that don't exist."""
    await _signup(client)
    r = await client.post(
        "/api/evaluations/arena/vote",
        json={"model_a": "fake-a", "model_b": "does-not-exist", "winner": "a"},
    )
    assert r.status_code == 403


async def test_arena_vote_blocks_inaccessible_model(client):
    await _signup(client)  # alice = admin
    # Restrict a real model to a group the non-admin user is not in.
    g = (await client.post("/api/admin/groups", json={"name": "vip"})).json()
    await client.put("/api/admin/model_access", json={"model_id": "fake-a", "group_ids": [g["id"]]})
    await client.post("/api/admin/users", json={"username": "bob", "password": "passpass", "role": "user"})
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": "bob", "password": "passpass"})

    r = await client.post(
        "/api/evaluations/arena/vote",
        json={"model_a": "fake-a", "model_b": "fake-b", "winner": "a"},
    )
    assert r.status_code == 403  # fake-a is filtered out of bob's available set


async def test_arena_votes_admin_only(client):
    await _signup(client)
    await client.post(
        "/api/evaluations/arena/vote",
        json={"model_a": "fake-a", "model_b": "fake-b", "winner": "a", "prompt": "hello world"},
    )
    rows = (await client.get("/api/evaluations/arena/votes")).json()
    assert len(rows) == 1 and rows[0]["winner"] == "a" and rows[0]["prompt"] == "hello world"
    assert rows[0]["username"] == "alice"

    await client.post("/api/admin/users", json={"username": "bob", "password": "passpass", "role": "user"})
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": "bob", "password": "passpass"})
    assert (await client.get("/api/evaluations/arena/votes")).status_code == 403
