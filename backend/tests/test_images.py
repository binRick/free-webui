"""Image generation: provider clients, the status endpoint, tool gating,
and the `imagine` tool wired through the streaming tool loop."""
import base64
import json

import httpx


def _mock_client(handler):
    return lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _signup(client):
    await client.post(
        "/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"}
    )


# ---- size parsing ----

def test_parse_size(monkeypatch):
    from app import images
    from app.config import settings

    monkeypatch.setattr(settings, "image_max_dimension", 2048)
    assert images._parse_size("256x384") == (256, 384)
    assert images._parse_size("1024X1024") == (1024, 1024)
    monkeypatch.setattr(settings, "image_size", "640x480")
    assert images._parse_size(None) == (640, 480)
    assert images._parse_size("garbage") == (640, 480)
    # non-positive / hex-looking values fall through to the default
    assert images._parse_size("0x10") == (640, 480)
    assert images._parse_size("-5x-5") == (640, 480)
    # clamped to [64, image_max_dimension]
    assert images._parse_size("4096x4096") == (2048, 2048)
    assert images._parse_size("10x10") == (64, 64)


# ---- backends ----

async def test_openai_backend(monkeypatch):
    from app import images
    from app.config import settings

    monkeypatch.setattr(settings, "image_backend", "openai")
    monkeypatch.setattr(settings, "image_base_url", "https://api.test/v1")
    monkeypatch.setattr(settings, "image_api_key", "sk-test")
    monkeypatch.setattr(settings, "image_model", "dall-e-3")

    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["auth"] = req.headers.get("authorization")
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json={"data": [{"b64_json": "QUJD"}]})

    monkeypatch.setattr(images, "_client", _mock_client(handler))
    url = await images.generate("a cat", size="512x512")
    assert url == "data:image/png;base64,QUJD"
    assert captured["url"].endswith("/images/generations")
    assert captured["auth"] == "Bearer sk-test"
    assert captured["body"]["size"] == "512x512"
    assert captured["body"]["model"] == "dall-e-3"
    assert captured["body"]["response_format"] == "b64_json"


async def test_openai_url_response(monkeypatch):
    """Backends that return a url (rather than b64) get fetched + encoded."""
    from app import images
    from app.config import settings

    monkeypatch.setattr(settings, "image_backend", "openai")
    monkeypatch.setattr(settings, "image_base_url", "https://api.test/v1")
    monkeypatch.setattr(settings, "image_api_key", "")

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/images/generations"):
            return httpx.Response(200, json={"data": [{"url": "https://img.test/x.png"}]})
        return httpx.Response(200, content=b"\x89PNG-bytes", headers={"content-type": "image/png"})

    monkeypatch.setattr(images, "_client", _mock_client(handler))
    url = await images.generate("a dog")
    assert url.startswith("data:image/png;base64,")


async def test_openai_http_error(monkeypatch):
    from app import images
    from app.config import settings

    monkeypatch.setattr(settings, "image_backend", "openai")
    monkeypatch.setattr(settings, "image_base_url", "https://api.test/v1")
    monkeypatch.setattr(
        images, "_client", _mock_client(lambda req: httpx.Response(429, text="slow down"))
    )
    try:
        await images.generate("a cat")
    except images.ImageError as e:
        assert "429" in str(e)
    else:
        raise AssertionError("expected ImageError")


async def test_automatic1111_backend(monkeypatch):
    from app import images
    from app.config import settings

    monkeypatch.setattr(settings, "image_backend", "automatic1111")
    monkeypatch.setattr(settings, "image_base_url", "http://sd.test")
    monkeypatch.setattr(settings, "image_steps", 25)

    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json={"images": ["QUJD"]})

    monkeypatch.setattr(images, "_client", _mock_client(handler))
    url = await images.generate("a robot", size="512x768", negative_prompt="blurry")
    assert url == "data:image/png;base64,QUJD"
    assert captured["url"].endswith("/sdapi/v1/txt2img")
    assert captured["body"]["width"] == 512 and captured["body"]["height"] == 768
    assert captured["body"]["negative_prompt"] == "blurry"
    assert captured["body"]["steps"] == 25


async def test_comfyui_backend(monkeypatch):
    from app import images
    from app.config import settings

    monkeypatch.setattr(settings, "image_backend", "comfyui")
    monkeypatch.setattr(settings, "image_base_url", "http://comfy.test")
    monkeypatch.setattr(settings, "comfyui_workflow_path", "")

    posted = {}

    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p.endswith("/prompt"):
            posted["graph"] = json.loads(req.content)["prompt"]
            return httpx.Response(200, json={"prompt_id": "pid1"})
        if "/history/" in p:
            return httpx.Response(
                200,
                json={
                    "pid1": {
                        "outputs": {
                            "9": {
                                "images": [
                                    {"filename": "out.png", "subfolder": "", "type": "output"}
                                ]
                            }
                        }
                    }
                },
            )
        if p.endswith("/view"):
            return httpx.Response(200, content=b"PNGDATA", headers={"content-type": "image/png"})
        return httpx.Response(404)

    monkeypatch.setattr(images, "_client", _mock_client(handler))
    url = await images.generate("a castle", size="64x96", negative_prompt="ugly")
    assert url.startswith("data:image/png;base64,")
    # Placeholders were substituted into the default graph.
    g = posted["graph"]
    assert g["6"]["inputs"]["text"] == "a castle"
    assert g["7"]["inputs"]["text"] == "ugly"
    assert g["5"]["inputs"]["width"] == 64 and g["5"]["inputs"]["height"] == 96
    assert isinstance(g["3"]["inputs"]["seed"], int)
    assert g["3"]["inputs"]["steps"] == settings.image_steps


async def test_comfyui_no_image_errors(monkeypatch):
    from app import images
    from app.config import settings

    monkeypatch.setattr(settings, "image_backend", "comfyui")
    monkeypatch.setattr(settings, "image_base_url", "http://comfy.test")

    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p.endswith("/prompt"):
            return httpx.Response(200, json={"prompt_id": "pid1"})
        if "/history/" in p:
            return httpx.Response(200, json={"pid1": {"outputs": {"9": {}}}})
        return httpx.Response(404)

    monkeypatch.setattr(images, "_client", _mock_client(handler))
    try:
        await images.generate("x")
    except images.ImageError as e:
        assert "no image" in str(e)
    else:
        raise AssertionError("expected ImageError")


async def test_generate_disabled(monkeypatch):
    from app import images
    from app.config import settings

    monkeypatch.setattr(settings, "image_backend", "")
    try:
        await images.generate("x")
    except images.ImageError as e:
        assert "not configured" in str(e)
    else:
        raise AssertionError("expected ImageError")


# ---- status endpoint ----

async def test_image_status(client, monkeypatch):
    from app.config import settings

    await _signup(client)
    monkeypatch.setattr(settings, "image_backend", "")
    assert (await client.get("/api/images/status")).json() == {
        "available": False,
        "backend": None,
    }
    monkeypatch.setattr(settings, "image_backend", "openai")
    assert (await client.get("/api/images/status")).json() == {
        "available": True,
        "backend": "openai",
    }


# ---- tool gating ----

def test_imagine_gated_on_backend(monkeypatch):
    from app import tools

    monkeypatch.setattr(tools.settings, "image_backend", "")
    names = [s["function"]["name"] for s in tools.builtin_tool_specs()]
    assert "imagine" not in names
    assert "calculate" in names and "now" in names

    monkeypatch.setattr(tools.settings, "image_backend", "openai")
    names = [s["function"]["name"] for s in tools.builtin_tool_specs()]
    assert "imagine" in names


# ---- imagine through the streaming tool loop ----

async def test_imagine_tool_loop(client, monkeypatch):
    from app import images
    from app.config import settings
    from app.main import app

    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    await client.patch(f"/api/conversations/{cid}", json={"tools_enabled": True})

    monkeypatch.setattr(settings, "image_backend", "openai")
    monkeypatch.setattr(settings, "image_base_url", "https://api.test/v1")
    monkeypatch.setattr(
        images,
        "_client",
        _mock_client(lambda req: httpx.Response(200, json={"data": [{"b64_json": "QUJD"}]})),
    )

    call_count = {"n": 0}
    seen: list[dict] = []

    def _stream_chunks(chunks: list[dict]) -> httpx.Response:
        body = b""
        for c in chunks:
            body += f"data: {json.dumps(c)}\n\n".encode()
        body += b"data: [DONE]\n\n"
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    async def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/chat/completions"):
            from tests.conftest import _fake_handler
            return await _fake_handler(request)
        call_count["n"] += 1
        seen.append(json.loads(request.content))
        if call_count["n"] == 1:
            return _stream_chunks([
                {
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": "call_i",
                                "type": "function",
                                "function": {
                                    "name": "imagine",
                                    "arguments": "{\"prompt\": \"a sunset over the sea\"}",
                                },
                            }]
                        }
                    }]
                },
                {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            ])
        return _stream_chunks([
            {"choices": [{"delta": {"content": "Here is your image."}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ])

    app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://upstream/v1"
    )

    tool_events: list[dict] = []
    image_events: list[dict] = []
    deltas: list[str] = []
    async with client.stream(
        "POST", f"/api/conversations/{cid}/messages", json={"content": "draw a sunset"}
    ) as r:
        raw = b""
        async for chunk in r.aiter_bytes():
            raw += chunk
    text = raw.decode()

    for frame in text.split("\n\n"):
        frame = frame.strip()
        if not frame:
            continue
        kind = None
        for line in frame.splitlines():
            if line.startswith("event:"):
                kind = line[6:].strip()
            elif line.startswith("data:"):
                d = line[5:].strip()
                if d == "[DONE]":
                    continue
                payload = json.loads(d)
                if kind == "tool_call":
                    tool_events.append(payload)
                elif kind == "image":
                    image_events.append(payload)
                else:
                    c = payload.get("choices", [{}])[0].get("delta", {}).get("content")
                    if isinstance(c, str):
                        deltas.append(c)

    # imagine was offered to the model
    assert "imagine" in [t["function"]["name"] for t in seen[0]["tools"]]
    # the generated image was surfaced over SSE, after the tool_call frame
    assert image_events == [{"url": "data:image/png;base64,QUJD"}]
    assert text.index("event: tool_call") < text.index("event: image")
    # the tool call was surfaced with a (text-only) confirmation result
    assert tool_events and tool_events[0]["name"] == "imagine"
    assert "displayed to the user" in tool_events[0]["result"]
    assert "".join(deltas) == "Here is your image."
    assert call_count["n"] == 2

    # persisted as a multimodal assistant message; the image is externalized to
    # the object store (a /api/files ref), not the inline base64 payload.
    conv = (await client.get(f"/api/conversations/{cid}")).json()
    last = conv["messages"][-1]
    assert last["role"] == "assistant"
    content = json.loads(last["content"])
    assert "base64" not in last["content"]
    img = next(p for p in content if p.get("type") == "image_url")
    ref = img["image_url"]["url"]
    assert ref.startswith("/api/files/")
    assert any(p.get("type") == "text" and p["text"] == "Here is your image." for p in content)
    # the ref serves the original bytes (QUJD == base64 of "ABC")
    fr = await client.get(ref)
    assert fr.status_code == 200 and fr.content == b"ABC"


# ---- additional provider error / edge-case coverage ----

async def test_openai_gpt_image_omits_response_format(monkeypatch):
    """gpt-image-1 rejects response_format, so it must not be sent."""
    from app import images
    from app.config import settings

    monkeypatch.setattr(settings, "image_backend", "openai")
    monkeypatch.setattr(settings, "image_base_url", "https://api.test/v1")
    monkeypatch.setattr(settings, "image_api_key", "")
    monkeypatch.setattr(settings, "image_model", "gpt-image-1")

    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json={"data": [{"b64_json": "QUJD"}]})

    monkeypatch.setattr(images, "_client", _mock_client(handler))
    url = await images.generate("a cat")
    assert url == "data:image/png;base64,QUJD"
    assert "response_format" not in captured["body"]
    assert captured["body"]["model"] == "gpt-image-1"


async def test_automatic1111_errors(monkeypatch):
    from app import images
    from app.config import settings

    monkeypatch.setattr(settings, "image_backend", "automatic1111")
    monkeypatch.setattr(settings, "image_base_url", "http://sd.test")

    monkeypatch.setattr(images, "_client", _mock_client(lambda req: httpx.Response(500, text="boom")))
    try:
        await images.generate("x", size="64x64")
    except images.ImageError as e:
        assert "500" in str(e)
    else:
        raise AssertionError("expected ImageError on http 500")

    monkeypatch.setattr(images, "_client", _mock_client(lambda req: httpx.Response(200, json={"images": []})))
    try:
        await images.generate("x", size="64x64")
    except images.ImageError as e:
        assert "empty" in str(e)
    else:
        raise AssertionError("expected ImageError on empty images")


async def test_network_error_wrapped_as_image_error(monkeypatch):
    """Transport-level failures must surface as ImageError with a non-empty,
    class-named message (raw httpx timeouts can stringify to '')."""
    from app import images
    from app.config import settings

    monkeypatch.setattr(settings, "image_backend", "automatic1111")
    monkeypatch.setattr(settings, "image_base_url", "http://sd.test")

    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("", request=req)

    monkeypatch.setattr(images, "_client", _mock_client(boom))
    try:
        await images.generate("x", size="64x64")
    except images.ImageError as e:
        assert "ReadTimeout" in str(e) and "request failed" in str(e)
    else:
        raise AssertionError("expected ImageError wrapping the transport error")


async def test_comfyui_timeout(monkeypatch):
    from app import images
    from app.config import settings

    monkeypatch.setattr(settings, "image_backend", "comfyui")
    monkeypatch.setattr(settings, "image_base_url", "http://comfy.test")
    monkeypatch.setattr(settings, "image_timeout_seconds", 1.0)

    async def _nosleep(*a, **k):
        return None

    monkeypatch.setattr(images.asyncio, "sleep", _nosleep)

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/prompt"):
            return httpx.Response(200, json={"prompt_id": "pid1"})
        if "/history/" in req.url.path:
            return httpx.Response(200, json={})  # prompt never appears
        return httpx.Response(404)

    monkeypatch.setattr(images, "_client", _mock_client(handler))
    try:
        await images.generate("x", size="64x64")
    except images.ImageError as e:
        assert "timed out" in str(e)
    else:
        raise AssertionError("expected timeout ImageError")


async def test_comfyui_completed_but_empty(monkeypatch):
    """A completed prompt with no outputs is distinct from a timeout."""
    from app import images
    from app.config import settings

    monkeypatch.setattr(settings, "image_backend", "comfyui")
    monkeypatch.setattr(settings, "image_base_url", "http://comfy.test")

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/prompt"):
            return httpx.Response(200, json={"prompt_id": "pid1"})
        if "/history/" in req.url.path:
            return httpx.Response(200, json={"pid1": {"outputs": {}}})
        return httpx.Response(404)

    monkeypatch.setattr(images, "_client", _mock_client(handler))
    try:
        await images.generate("x", size="64x64")
    except images.ImageError as e:
        assert "no image outputs" in str(e) and "timed out" not in str(e)
    else:
        raise AssertionError("expected completed-but-empty ImageError")


async def test_comfyui_embedded_numeric_token(monkeypatch):
    """A numeric token embedded in a larger string (custom workflow) is
    substituted, not left literal."""
    from app import images
    from app.config import settings

    monkeypatch.setattr(settings, "image_backend", "comfyui")
    monkeypatch.setattr(settings, "image_base_url", "http://comfy.test")

    workflow = {"9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "fw-%seed%", "images": ["8", 0]}}}
    posted = {}

    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p.endswith("/prompt"):
            posted["graph"] = json.loads(req.content)["prompt"]
            return httpx.Response(200, json={"prompt_id": "pid1"})
        if "/history/" in p:
            return httpx.Response(200, json={"pid1": {"outputs": {"9": {"images": [{"filename": "o.png"}]}}}})
        if p.endswith("/view"):
            return httpx.Response(200, content=b"PNG", headers={"content-type": "image/png"})
        return httpx.Response(404)

    monkeypatch.setattr(images, "_load_comfy_workflow", lambda: json.loads(json.dumps(workflow)))
    monkeypatch.setattr(images, "_client", _mock_client(handler))
    await images.generate("a tree", size="64x64")
    prefix = posted["graph"]["9"]["inputs"]["filename_prefix"]
    assert prefix.startswith("fw-") and "%seed%" not in prefix
    assert prefix.split("-", 1)[1].isdigit()


async def test_image_size_cap(monkeypatch):
    from app import images
    from app.config import settings

    monkeypatch.setattr(settings, "image_backend", "automatic1111")
    monkeypatch.setattr(settings, "image_base_url", "http://sd.test")
    monkeypatch.setattr(settings, "image_max_bytes", 4)  # 4-byte ceiling
    # 8 base64 chars ~= 6 decoded bytes, over the cap.
    monkeypatch.setattr(
        images, "_client", _mock_client(lambda req: httpx.Response(200, json={"images": ["QUJDRUZH"]}))
    )
    try:
        await images.generate("x", size="64x64")
    except images.ImageError as e:
        assert "too large" in str(e)
    else:
        raise AssertionError("expected ImageError on oversized image")


# ---- shared streaming harness for tool-loop integration tests ----

def _stream_chunks(chunks: list[dict]) -> httpx.Response:
    body = b""
    for c in chunks:
        body += f"data: {json.dumps(c)}\n\n".encode()
    body += b"data: [DONE]\n\n"
    return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})


def _parse_sse(text: str):
    tool_events, image_events, deltas, order = [], [], [], []
    for frame in text.split("\n\n"):
        frame = frame.strip()
        if not frame:
            continue
        kind = None
        for line in frame.splitlines():
            if line.startswith("event:"):
                kind = line[6:].strip()
            elif line.startswith("data:"):
                d = line[5:].strip()
                if d == "[DONE]":
                    continue
                payload = json.loads(d)
                if kind == "tool_call":
                    tool_events.append(payload)
                    order.append("tool_call")
                elif kind == "image":
                    image_events.append(payload)
                    order.append("image")
                else:
                    c = payload.get("choices", [{}])[0].get("delta", {}).get("content")
                    if isinstance(c, str):
                        deltas.append(c)
                        order.append("content")
    return tool_events, image_events, deltas, order


async def _read_stream(client, cid: str, content: str = "go") -> str:
    async with client.stream(
        "POST", f"/api/conversations/{cid}/messages", json={"content": content}
    ) as r:
        raw = b""
        async for chunk in r.aiter_bytes():
            raw += chunk
    return raw.decode()


async def test_imagine_image_only_persistence(client, monkeypatch):
    """imagine produces an image and the model emits no follow-up text: the
    assistant message must persist as an image-only multimodal message."""
    from app import images
    from app.config import settings
    from app.main import app

    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    await client.patch(f"/api/conversations/{cid}", json={"tools_enabled": True})
    monkeypatch.setattr(settings, "image_backend", "openai")
    monkeypatch.setattr(settings, "image_base_url", "https://api.test/v1")
    monkeypatch.setattr(
        images, "_client",
        _mock_client(lambda req: httpx.Response(200, json={"data": [{"b64_json": "QUJD"}]})),
    )

    count = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/chat/completions"):
            from tests.conftest import _fake_handler
            return await _fake_handler(request)
        count["n"] += 1
        if count["n"] == 1:
            return _stream_chunks([
                {"choices": [{"delta": {"tool_calls": [{
                    "index": 0, "id": "c1", "type": "function",
                    "function": {"name": "imagine", "arguments": "{\"prompt\": \"a tree\"}"},
                }]}}]},
                {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            ])
        # no content at all — just stop
        return _stream_chunks([{"choices": [{"delta": {}, "finish_reason": "stop"}]}])

    app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://upstream/v1"
    )

    text = await _read_stream(client, cid, "draw a tree")
    _tool, image_events, deltas, _order = _parse_sse(text)
    assert image_events == [{"url": "data:image/png;base64,QUJD"}]
    assert "".join(deltas) == ""

    conv = (await client.get(f"/api/conversations/{cid}")).json()
    last = conv["messages"][-1]
    assert last["role"] == "assistant"
    content = json.loads(last["content"])
    assert len(content) == 1 and content[0]["type"] == "image_url"
    ref = content[0]["image_url"]["url"]
    assert ref.startswith("/api/files/")
    assert (await client.get(ref)).content == b"ABC"
    assert not any(p.get("type") == "text" for p in content)


async def test_imagine_multiple_images_in_one_turn(client, monkeypatch):
    """Two imagine calls in one assistant turn -> two image frames + two
    persisted image parts."""
    from app import images
    from app.config import settings
    from app.main import app

    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    await client.patch(f"/api/conversations/{cid}", json={"tools_enabled": True})
    monkeypatch.setattr(settings, "image_backend", "openai")
    monkeypatch.setattr(settings, "image_base_url", "https://api.test/v1")

    img_count = {"n": 0}

    def img_handler(req: httpx.Request) -> httpx.Response:
        img_count["n"] += 1
        return httpx.Response(200, json={"data": [{"b64_json": f"IMG{img_count['n']}"}]})

    monkeypatch.setattr(images, "_client", _mock_client(img_handler))

    count = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/chat/completions"):
            from tests.conftest import _fake_handler
            return await _fake_handler(request)
        count["n"] += 1
        if count["n"] == 1:
            return _stream_chunks([
                {"choices": [{"delta": {"tool_calls": [
                    {"index": 0, "id": "c1", "type": "function",
                     "function": {"name": "imagine", "arguments": "{\"prompt\": \"a\"}"}},
                    {"index": 1, "id": "c2", "type": "function",
                     "function": {"name": "imagine", "arguments": "{\"prompt\": \"b\"}"}},
                ]}}]},
                {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            ])
        return _stream_chunks([
            {"choices": [{"delta": {"content": "done"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ])

    app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://upstream/v1"
    )

    text = await _read_stream(client, cid, "two pics")
    _tool, image_events, _deltas, _order = _parse_sse(text)
    urls = [e["url"] for e in image_events]
    assert urls == ["data:image/png;base64,IMG1", "data:image/png;base64,IMG2"]

    conv = (await client.get(f"/api/conversations/{cid}")).json()
    content = json.loads(conv["messages"][-1]["content"])
    refs = [p["image_url"]["url"] for p in content if p.get("type") == "image_url"]
    assert len(refs) == 2 and all(r.startswith("/api/files/") for r in refs)
    served = [(await client.get(r)).content for r in refs]
    assert served == [base64.b64decode("IMG1"), base64.b64decode("IMG2")]


async def test_generated_image_not_replayed_upstream(client, monkeypatch):
    """A persisted multimodal assistant message must be downcast to text when
    replayed to the upstream on the next turn (no image_url in assistant history)."""
    from app import images
    from app.config import settings
    from app.main import app

    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    await client.patch(f"/api/conversations/{cid}", json={"tools_enabled": True})
    monkeypatch.setattr(settings, "image_backend", "openai")
    monkeypatch.setattr(settings, "image_base_url", "https://api.test/v1")
    monkeypatch.setattr(
        images, "_client",
        _mock_client(lambda req: httpx.Response(200, json={"data": [{"b64_json": "QUJD"}]})),
    )

    count = {"n": 0}
    seen: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/chat/completions"):
            from tests.conftest import _fake_handler
            return await _fake_handler(request)
        count["n"] += 1
        seen.append(json.loads(request.content))
        if count["n"] == 1:
            return _stream_chunks([
                {"choices": [{"delta": {"tool_calls": [{
                    "index": 0, "id": "c1", "type": "function",
                    "function": {"name": "imagine", "arguments": "{\"prompt\": \"a cat\"}"},
                }]}}]},
                {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            ])
        return _stream_chunks([
            {"choices": [{"delta": {"content": "here"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ])

    app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://upstream/v1"
    )

    # turn 1 generates + persists a multimodal assistant message
    await _read_stream(client, cid, "draw a cat")
    # turn 2: the persisted assistant message is now part of history
    await _read_stream(client, cid, "thanks!")

    # the final upstream payload's assistant message(s) must be plain strings,
    # never carrying the image_url data URL
    final_payload = seen[-1]
    assistant_msgs = [m for m in final_payload["messages"] if m["role"] == "assistant"]
    assert assistant_msgs, "expected the prior assistant turn in history"
    for m in assistant_msgs:
        assert isinstance(m["content"], str)
        assert "data:image" not in m["content"]


async def test_imagine_disabled_not_offered(client, monkeypatch):
    """With tools enabled but no image backend, imagine must be absent from the
    tools actually sent upstream (compose_tool_specs end-to-end)."""
    from app.config import settings
    from app.main import app

    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    await client.patch(f"/api/conversations/{cid}", json={"tools_enabled": True})
    monkeypatch.setattr(settings, "image_backend", "")

    seen: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/chat/completions"):
            from tests.conftest import _fake_handler
            return await _fake_handler(request)
        seen.append(json.loads(request.content))
        return _stream_chunks([
            {"choices": [{"delta": {"content": "hi"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ])

    app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://upstream/v1"
    )

    await _read_stream(client, cid, "hello")
    names = [t["function"]["name"] for t in seen[0]["tools"]]
    assert "imagine" not in names
    assert "calculate" in names and "now" in names
