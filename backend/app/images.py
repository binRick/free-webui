"""Image generation. Proxies a text prompt to one of three backends —
OpenAI Images, AUTOMATIC1111, or ComfyUI — and returns a `data:` URL so the
result can ride inside an ordinary multimodal chat message (no extra storage
or auth-scoped media endpoint needed).

Exposed to the LLM through the built-in `imagine` tool (see tools.py); the
tool loop in conversations.py surfaces and persists whatever this returns.
"""
from __future__ import annotations

import asyncio
import base64
import json
import random
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .auth import current_user
from .config import settings
from .netguard import BlockedURLError, check_url

router = APIRouter(prefix="/api/images", tags=["images"])


class ImageError(Exception):
    """Raised when image generation fails; surfaced to the LLM as a tool error."""


class ImageStatus(BaseModel):
    available: bool
    backend: str | None = None


@router.get("/status", response_model=ImageStatus, dependencies=[Depends(current_user)])
async def status_endpoint() -> ImageStatus:
    if not settings.image_backend:
        return ImageStatus(available=False)
    return ImageStatus(available=True, backend=settings.image_backend)


# ---- helpers ----

def _parse_size(size: str | None) -> tuple[int, int]:
    """Parse a "WxH" string into (width, height), falling back to the
    configured default, then to 512x512. Non-positive / unparseable values
    fall through; valid dimensions are clamped to [64, image_max_dimension]
    so a user-supplied size can't drive a huge job on the image backend."""
    cap = settings.image_max_dimension or 2048
    for candidate in (size, settings.image_size, "512x512"):
        if not candidate:
            continue
        try:
            w_str, h_str = candidate.lower().split("x", 1)
            w, h = int(w_str), int(h_str)
        except (ValueError, AttributeError):
            continue
        if w <= 0 or h <= 0:
            continue
        return max(64, min(w, cap)), max(64, min(h, cap))
    return 512, 512


def _enforce_size(n_bytes: int) -> None:
    limit = settings.image_max_bytes
    if limit and n_bytes > limit:
        raise ImageError(f"generated image too large ({n_bytes} bytes > {limit} limit)")


def _b64_to_data_url(b64: str, mime: str = "image/png") -> str:
    # Some backends hand back a full data URL already; normalise to bare b64.
    if b64.startswith("data:"):
        _enforce_size(len(b64))
        return b64
    _enforce_size((len(b64) * 3) // 4)  # approx decoded size
    return f"data:{mime};base64,{b64}"


def _bytes_to_data_url(raw: bytes, mime: str = "image/png") -> str:
    _enforce_size(len(raw))
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=settings.image_timeout_seconds)


# ---- backends ----

async def _openai(prompt: str, negative_prompt: str | None, size_str: str) -> str:
    base = settings.image_base_url.rstrip("/") or "https://api.openai.com/v1"
    headers = {"content-type": "application/json"}
    if settings.image_api_key:
        headers["authorization"] = f"Bearer {settings.image_api_key}"
    body: dict[str, Any] = {
        "model": settings.image_model,
        "prompt": prompt,
        "n": 1,
        "size": size_str,
    }
    # gpt-image-1 rejects response_format (always returns b64). For dall-e and
    # most OpenAI-compatible servers, asking for b64 avoids a second fetch.
    if not settings.image_model.startswith("gpt-image"):
        body["response_format"] = "b64_json"
    async with _client() as c:
        r = await c.post(f"{base}/images/generations", json=body, headers=headers)
    if r.status_code >= 400:
        raise ImageError(f"openai images http {r.status_code}: {r.text[:200]}")
    try:
        data = r.json().get("data") or []
    except ValueError:
        raise ImageError("openai images: non-JSON response")
    if not data:
        raise ImageError("openai images: empty response")
    item = data[0]
    if item.get("b64_json"):
        return _b64_to_data_url(item["b64_json"])
    if item.get("url"):
        # The result URL comes back from the image backend; guard it before we
        # fetch server-side so it can't be used to reach internal/metadata hosts.
        try:
            await check_url(item["url"])
        except BlockedURLError as e:
            raise ImageError(str(e))
        async with _client() as c:
            img = await c.get(item["url"])
        if img.status_code >= 400:
            raise ImageError(f"openai images: could not fetch result url ({img.status_code})")
        return _bytes_to_data_url(img.content, img.headers.get("content-type", "image/png"))
    raise ImageError("openai images: response had neither b64_json nor url")


async def _automatic1111(prompt: str, negative_prompt: str | None, w: int, h: int) -> str:
    base = settings.image_base_url.rstrip("/")
    if not base:
        raise ImageError("automatic1111: FREE_WEBUI_IMAGE_BASE_URL is not set")
    body: dict[str, Any] = {
        "prompt": prompt,
        "negative_prompt": negative_prompt or "",
        "steps": settings.image_steps,
        "width": w,
        "height": h,
    }
    async with _client() as c:
        r = await c.post(f"{base}/sdapi/v1/txt2img", json=body)
    if r.status_code >= 400:
        raise ImageError(f"automatic1111 http {r.status_code}: {r.text[:200]}")
    try:
        images = r.json().get("images") or []
    except ValueError:
        raise ImageError("automatic1111: non-JSON response")
    if not images:
        raise ImageError("automatic1111: empty response")
    return _b64_to_data_url(images[0])


# Minimal SD1.5 txt2img graph in ComfyUI's API ("prompt") format. Users with a
# different setup should point comfyui_workflow_path at their own export.
_DEFAULT_COMFY_WORKFLOW = {
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": "%seed%", "steps": "%steps%", "cfg": 7.0,
            "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
            "model": ["4", 0], "positive": ["6", 0],
            "negative": ["7", 0], "latent_image": ["5", 0],
        },
    },
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
    "5": {"class_type": "EmptyLatentImage", "inputs": {"width": "%width%", "height": "%height%", "batch_size": 1}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "%prompt%", "clip": ["4", 1]}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "%negative_prompt%", "clip": ["4", 1]}},
    "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "free-webui", "images": ["8", 0]}},
}


def _load_comfy_workflow() -> dict:
    if settings.comfyui_workflow_path:
        try:
            return json.loads(Path(settings.comfyui_workflow_path).read_text())
        except (OSError, ValueError) as e:
            raise ImageError(f"comfyui: could not read workflow template: {e}")
    return json.loads(json.dumps(_DEFAULT_COMFY_WORKFLOW))  # deep copy


def _substitute(node: Any, subs: dict[str, Any]) -> Any:
    """Recursively replace %token% placeholders. A string equal to a numeric
    token (e.g. "%width%") becomes that int; %prompt% is replaced in place."""
    if isinstance(node, dict):
        return {k: _substitute(v, subs) for k, v in node.items()}
    if isinstance(node, list):
        return [_substitute(v, subs) for v in node]
    if isinstance(node, str):
        if node in subs:  # exact token -> native type (int for numeric tokens)
            return subs[node]
        for token, value in subs.items():  # embedded token -> stringified
            if token in node:
                node = node.replace(token, str(value))
        return node
    return node


async def _comfyui(prompt: str, negative_prompt: str | None, w: int, h: int) -> str:
    base = settings.image_base_url.rstrip("/")
    if not base:
        raise ImageError("comfyui: FREE_WEBUI_IMAGE_BASE_URL is not set")
    seed = random.randint(1, 2**32 - 1)
    graph = _substitute(
        _load_comfy_workflow(),
        {
            "%prompt%": prompt,
            "%negative_prompt%": negative_prompt or "",
            "%width%": w,
            "%height%": h,
            "%seed%": seed,
            "%steps%": settings.image_steps,
        },
    )
    async with _client() as c:
        r = await c.post(f"{base}/prompt", json={"prompt": graph})
        if r.status_code >= 400:
            raise ImageError(f"comfyui http {r.status_code}: {r.text[:200]}")
        try:
            prompt_id = r.json()["prompt_id"]
        except (ValueError, KeyError):
            raise ImageError("comfyui: /prompt did not return a prompt_id")

        # Poll history until this prompt's outputs are present. A prompt_id
        # appearing under /history is ComfyUI's completion signal.
        deadline_polls = max(1, int(settings.image_timeout_seconds / 0.5))
        completed = False
        outputs: dict = {}
        for _ in range(deadline_polls):
            h_resp = await c.get(f"{base}/history/{prompt_id}")
            if h_resp.status_code < 400:
                hist = h_resp.json()
                if prompt_id in hist:
                    completed = True
                    outputs = hist[prompt_id].get("outputs", {})
                    break
            await asyncio.sleep(0.5)
        if not completed:
            raise ImageError("comfyui: timed out waiting for image")
        if not outputs:
            raise ImageError(
                "comfyui: prompt completed with no image outputs "
                "(check the workflow has a SaveImage node and did not error)"
            )

        image_ref = next(
            (
                img
                for node in outputs.values()
                for img in node.get("images", [])
                if img.get("filename")
            ),
            None,
        )
        if not image_ref:
            raise ImageError("comfyui: no image in outputs")
        view = await c.get(
            f"{base}/view",
            params={
                "filename": image_ref["filename"],
                "subfolder": image_ref.get("subfolder", ""),
                "type": image_ref.get("type", "output"),
            },
        )
    if view.status_code >= 400:
        raise ImageError(f"comfyui: could not fetch image ({view.status_code})")
    return _bytes_to_data_url(view.content, view.headers.get("content-type", "image/png"))


async def generate(
    prompt: str,
    *,
    size: str | None = None,
    negative_prompt: str | None = None,
) -> str:
    """Generate one image and return it as a `data:` URL. Raises ImageError."""
    backend = settings.image_backend
    if not backend:
        raise ImageError("image generation is not configured on this server")
    if not prompt.strip():
        raise ImageError("missing prompt")
    w, h = _parse_size(size)
    size_str = f"{w}x{h}"
    try:
        if backend == "openai":
            return await _openai(prompt, negative_prompt, size_str)
        if backend == "automatic1111":
            return await _automatic1111(prompt, negative_prompt, w, h)
        if backend == "comfyui":
            return await _comfyui(prompt, negative_prompt, w, h)
    except httpx.HTTPError as e:
        # Transport-level failures (timeouts, connection errors) — some
        # stringify to empty, so include the class name for a useful message.
        raise ImageError(f"{backend}: request failed: {type(e).__name__}: {e}")
    raise ImageError(f"unknown image backend {backend!r}")
