"""Pipelines / plugin framework — operator-installed Python middleware that
hooks the chat flow.

A plugin is a `*.py` file in FREE_WEBUI_PLUGINS_DIR exposing one or both async
hooks plus an optional priority:

    PRIORITY = 10  # lower runs first on inlet; outlet runs in reverse

    async def inlet(body: dict, ctx: PluginContext) -> dict | None:
        '''Mutate the outbound OpenAI request before it hits the upstream.'''
        ...

    async def outlet(text: str, ctx: PluginContext) -> str | None:
        '''Mutate/observe the final assistant text before it is persisted.'''
        ...

TRUST MODEL: plugins are operator-installed, trusted, in-process code with full
backend access (DB, http client, settings, filesystem). This is the deliberate
OPPOSITE of the code interpreter, which sandboxes untrusted model/user code. Do
not load plugins from an untrusted source.

Failure isolation: every hook runs under a timeout in a try/except, on a COPY of
the request, and its result is committed only if it type-checks. A plugin that
raises, times out, or returns the wrong/off-contract type is logged and skipped
— the turn proceeds exactly as if the plugin weren't installed; raising never
aborts.

What v1 can and can't do: there is NO turn-blocking primitive. An inlet only
rewrites the upstream request — the model still runs (rewrite the messages to,
say, a refusal prompt to *steer* it). An outlet only rewrites the PERSISTED
text, and it runs AFTER that text has already streamed to the client, so it
cannot un-send what the user already saw — it only changes what a reload shows.
(A pre-upstream short-circuit and a per-chunk `stream` hook are deferred to v2+.)

The timeout fires only at await points: a synchronously blocking hook cannot be
interrupted and will stall the whole server, so hooks must do blocking I/O and
heavy CPU work in a thread (e.g. `await asyncio.to_thread(...)`).
"""
from __future__ import annotations

import asyncio
import copy
import importlib.util
import inspect
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from .auth import require_admin
from .config import settings

log = logging.getLogger("free_webui.plugins")

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


@dataclass
class PluginContext:
    """Threaded into every hook. Note: db is the single shared connection —
    treat it as read-mostly; heavy or transactional writes mid-turn can
    interleave with the framework's own persistence."""

    db: Any
    http: Any
    user_id: int | None
    conversation_id: str
    model: str


@dataclass
class _Plugin:
    name: str
    priority: int
    inlet: Callable[..., Awaitable[Any]] | None
    outlet: Callable[..., Awaitable[Any]] | None
    error: str | None = None


class PluginRecord(BaseModel):
    name: str
    priority: int
    has_inlet: bool
    has_outlet: bool
    error: str | None = None


class PluginRegistry:
    """Loaded plugins, sorted once. inlets run ascending by (priority, name);
    outlets run in the reverse order so wrappers nest symmetrically."""

    def __init__(self) -> None:
        self._plugins: list[_Plugin] = []

    def _add(self, p: _Plugin) -> None:
        self._plugins.append(p)

    def _finalize(self) -> None:
        self._plugins.sort(key=lambda p: (p.priority, p.name))

    @property
    def inlets(self) -> list[_Plugin]:
        return [p for p in self._plugins if p.inlet is not None]

    @property
    def outlets(self) -> list[_Plugin]:
        return [p for p in reversed(self._plugins) if p.outlet is not None]

    def records(self) -> list[PluginRecord]:
        return [
            PluginRecord(
                name=p.name,
                priority=p.priority,
                has_inlet=p.inlet is not None,
                has_outlet=p.outlet is not None,
                error=p.error,
            )
            for p in self._plugins
        ]

    def __bool__(self) -> bool:
        return any(p.inlet or p.outlet for p in self._plugins)


def load(plugins_dir: str | None) -> PluginRegistry:
    """Discover and import plugin modules from `plugins_dir`. Returns an empty
    registry when unset/missing (the feature is gated off). Import or validation
    errors are recorded per-file rather than raised, so one bad plugin can't
    stop the app from starting."""
    reg = PluginRegistry()
    if not plugins_dir:
        return reg
    directory = Path(plugins_dir)
    if not directory.is_dir():
        log.warning("plugins dir %r does not exist; no plugins loaded", plugins_dir)
        return reg

    seen: set[str] = set()
    for path in sorted(directory.glob("*.py")):
        stem = path.stem
        if stem.startswith("_"):
            continue
        if stem in seen:
            reg._add(_Plugin(stem, 0, None, None, error="duplicate plugin name; skipped"))
            log.warning("duplicate plugin name %r; skipped", stem)
            continue
        seen.add(stem)
        modname = f"fw_plugin_{stem}"
        try:
            spec = importlib.util.spec_from_file_location(modname, path)
            module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            # Register before exec so dataclasses, typing.get_type_hints, and
            # pickling can resolve the module by name (the importlib-documented
            # pattern); pop it back out if the import fails.
            sys.modules[modname] = module
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        except Exception as e:  # noqa: BLE001 — any import error is recorded, not fatal
            sys.modules.pop(modname, None)
            reg._add(_Plugin(stem, 0, None, None, error=f"import failed: {e!r}"))
            log.warning("plugin %r failed to import: %r", stem, e)
            continue

        inlet = getattr(module, "inlet", None)
        outlet = getattr(module, "outlet", None)
        errs: list[str] = []
        if inlet is not None and not inspect.iscoroutinefunction(inlet):
            errs.append("inlet must be an async function")
            inlet = None
        if outlet is not None and not inspect.iscoroutinefunction(outlet):
            errs.append("outlet must be an async function")
            outlet = None
        if inlet is None and outlet is None:
            if errs:
                reg._add(_Plugin(stem, 0, None, None, error="; ".join(errs)))
            continue  # a module with no usable hooks is simply ignored
        try:
            priority = int(getattr(module, "PRIORITY", 0))
        except (TypeError, ValueError):
            priority = 0
        reg._add(_Plugin(stem, priority, inlet, outlet, error="; ".join(errs) or None))

    reg._finalize()
    if reg:
        log.info(
            "loaded %d plugin(s): %s",
            len(reg.records()),
            ", ".join(f"{r.name}(p={r.priority})" for r in reg.records()),
        )
    return reg


def _valid_inlet_body(body: dict) -> bool:
    """A committed body must keep `messages` a list and `model` (if present) a
    str. An off-contract value here would otherwise break the tool loop
    (`msgs.append(...)`) or the upstream POST, so such a result is discarded and
    the turn proceeds with the previous body. Missing keys are tolerated — the
    call site re-asserts a default model and keeps the prior message list."""
    msgs = body.get("messages")
    if msgs is not None and not isinstance(msgs, list):
        return False
    model = body.get("model")
    if model is not None and not isinstance(model, str):
        return False
    return True


async def run_inlet(registry: PluginRegistry | None, body: dict, ctx: PluginContext) -> dict:
    """Apply each inlet to the request body. Hands every plugin a deep copy and
    commits its result only on a clean return whose shape still type-checks — a
    failing or off-contract plugin leaves the body exactly as it was. The
    deep-copy itself runs inside the guard so even a non-copyable body can't
    abort the turn."""
    if not registry:
        return body
    for p in registry.inlets:
        t0 = time.monotonic()
        try:
            candidate = copy.deepcopy(body)
            result = await asyncio.wait_for(
                p.inlet(candidate, ctx), settings.plugins_timeout_seconds  # type: ignore[misc]
            )
        except Exception as e:  # noqa: BLE001 — isolate; never abort the turn
            _log_hook_failure(p.name, "inlet", ctx, t0, e)
            continue
        if result is None:
            proposed = candidate  # mutated in place
        elif isinstance(result, dict):
            proposed = result
        else:
            log.warning("plugin %r inlet returned %s; ignored", p.name, type(result).__name__)
            continue
        if not _valid_inlet_body(proposed):
            log.warning(
                "plugin %r inlet produced an off-contract body "
                "(messages must be a list, model a str); ignored",
                p.name,
            )
            continue
        body = proposed
    return body


async def run_outlet(registry: PluginRegistry | None, text: str, ctx: PluginContext) -> str:
    """Apply each outlet (reverse order) to the assistant text. Same isolation:
    only a clean, str-typed return is committed."""
    if not registry:
        return text
    for p in registry.outlets:
        t0 = time.monotonic()
        try:
            result = await asyncio.wait_for(
                p.outlet(text, ctx), settings.plugins_timeout_seconds  # type: ignore[misc]
            )
        except Exception as e:  # noqa: BLE001
            _log_hook_failure(p.name, "outlet", ctx, t0, e)
            continue
        if result is None:
            continue
        if isinstance(result, str):
            text = result
        else:
            log.warning("plugin %r outlet returned %s; ignored", p.name, type(result).__name__)
    return text


def _log_hook_failure(name: str, hook: str, ctx: PluginContext, t0: float, e: Exception) -> None:
    log.warning(
        "plugin %r %s failed (cid=%s, %.1fms): %r",
        name, hook, ctx.conversation_id, (time.monotonic() - t0) * 1000.0, e,
    )


@router.get("", response_model=list[PluginRecord], dependencies=[Depends(require_admin)])
async def list_plugins(request: Request) -> list[PluginRecord]:
    reg: PluginRegistry | None = getattr(request.app.state, "plugins", None)
    return reg.records() if reg else []
