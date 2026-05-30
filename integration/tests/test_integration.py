"""Live integration tests: the real free-webui backend against a real Ollama
model, over HTTP.

Models are non-deterministic, so the HARD assertions only check wiring and
behaviour (the stack is up, a turn streams non-empty content, the doc embeds,
the tool result is correct *if* the model called it, the plugin pipeline ran).
Anything that depends on the model actually being smart enough (recalling a RAG
fact, choosing to call a tool) is a SOFT check: it `xfail`s rather than failing,
so a small CPU model still gives a green, meaningful run.
"""
import os

import pytest

MODEL = os.environ.get("MODEL", "qwen2.5:1.5b")


async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


async def test_models_lists_upstream_model(admin):
    r = await admin.get("/api/models")
    assert r.status_code == 200
    ids = [m["id"] for m in r.json().get("data", [])]
    assert ids, "upstream returned no models"
    base = MODEL.split(":")[0]
    assert any(i == MODEL or i.startswith(base) for i in ids), f"{MODEL} not in {ids}"


async def test_chat_round_trip_streams_and_persists(admin, conversation, chat):
    text, _ = await chat(admin, conversation, "Reply with a short greeting.", temperature=0)
    assert text.strip(), "model streamed no content"

    full = await admin.get(f"/api/conversations/{conversation}")
    roles = [m["role"] for m in full.json()["messages"]]
    assert roles == ["user", "assistant"]


async def test_multiturn_keeps_history(admin, conversation, chat):
    await chat(admin, conversation, "My favourite colour is teal. Acknowledge briefly.", temperature=0)
    text, _ = await chat(admin, conversation, "What did I say my favourite colour was?", temperature=0)
    assert text.strip()

    full = await admin.get(f"/api/conversations/{conversation}")
    assert len(full.json()["messages"]) == 4  # 2 user + 2 assistant

    if "teal" not in text.lower():
        pytest.xfail("model did not recall the fact (non-deterministic at this model size)")


async def test_rag_upload_embeds_and_can_ground(admin, conversation, chat):
    # Uploading exercises the REAL /v1/embeddings call against Ollama; a failure
    # here means the embedding path is broken, independent of the chat model.
    doc = (
        b"Zibberland is a small fictional country. "
        b"The capital city of Zibberland is Quoxville."
    )
    up = await admin.post(
        f"/api/conversations/{conversation}/documents",
        files={"file": ("zibberland.txt", doc, "text/plain")},
    )
    assert up.status_code == 200, f"doc upload/embedding failed: {up.status_code} {up.text}"

    listed = await admin.get(f"/api/conversations/{conversation}/documents")
    assert listed.status_code == 200 and listed.json(), "no documents after upload"

    text, _ = await chat(
        admin, conversation,
        "What is the capital of Zibberland? Answer with just the city name.",
        temperature=0,
    )
    assert text.strip()
    if "quoxville" not in text.lower():
        pytest.xfail("model did not surface the RAG fact (non-deterministic at this model size)")


async def test_tool_loop_calculator(admin, conversation, chat):
    text, tools = await chat(
        admin, conversation,
        "Use the calculate tool to compute 17 * 23, then reply with just the number.",
        tools_enabled=True, temperature=0,
    )
    assert text.strip()

    calc = [t for t in tools if t.get("name") == "calculate"]
    if not calc:
        pytest.xfail("model did not invoke the calculate tool (non-deterministic)")

    # The integration win: the model drove the loop, free-webui dispatched the
    # tool, and fed a clean result back. That the tool RAN (not errored) is the
    # model-independent assertion; calculate's own correctness is pinned by the
    # unit tests in backend/tests/test_tools.py.
    result = calc[0].get("result", "")
    assert result and not result.startswith("error:"), f"tool errored: {calc[0]}"

    # Whether a 1.5B model picks the right expression is its own problem — only
    # hard-check the answer when it actually asked for 17*23.
    expr = calc[0].get("arguments", {}).get("expression", "").replace(" ", "")
    if expr != "17*23":
        pytest.xfail(f"model called the tool with a different expression: {calc[0]['arguments']}")
    assert result == "391" and "391" in text


async def test_plugin_outlet_marks_persisted_text(admin, conversation, chat):
    """Deterministic, model-independent: the harness mounts an outlet plugin
    that appends a marker. The live stream must NOT contain it (outlets only
    rewrite persisted text), but the stored assistant message must end with it."""
    marker = "<!-- via-plugin -->"
    text, _ = await chat(admin, conversation, "Say anything at all.", temperature=0)
    assert text.strip()
    assert marker not in text, "marker leaked into the live stream (outlet ran too early?)"

    full = await admin.get(f"/api/conversations/{conversation}")
    assistant = [m for m in full.json()["messages"] if m["role"] == "assistant"][-1]
    assert assistant["content"].rstrip().endswith(marker), (
        "plugin outlet marker missing from persisted assistant message — "
        "the plugin pipeline did not run end-to-end"
    )
