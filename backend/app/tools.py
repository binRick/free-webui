"""Built-in safe tools exposed to the LLM via OpenAI function-calling.

Adding a synchronous tool requires:
  1. A handler function that takes a dict of args and returns a string.
  2. An entry in TOOL_SPECS (OpenAI tool schema).
  3. An entry in HANDLERS keyed by tool name.

Async tools (which do I/O, e.g. `imagine`) live in ASYNC_HANDLERS and take a
ToolContext so they can surface artifacts (like generated images) back to the
streaming tool loop in conversations.py.
"""
from __future__ import annotations

import ast
import datetime
import operator
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from . import code_exec, images
from .config import settings


@dataclass
class ToolContext:
    """Side channel from a tool back to the streaming loop. Tools append any
    generated image `data:` URLs here; the loop emits + persists them."""

    images: list[str] = field(default_factory=list)


# OpenAI function/tool schemas for the always-on built-ins.
TOOL_SPECS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "now",
            "description": "Return the current date and time in ISO 8601, UTC.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": (
                "Evaluate a simple arithmetic expression. Supports +, -, *, /, **, %, "
                "parentheses, and unary minus. No variables, names, or function calls."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "e.g. \"(1 + 2) * 3\"",
                    }
                },
                "required": ["expression"],
            },
        },
    },
]


def _now(_args: dict[str, Any]) -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


_MAX_POW_EXPONENT = 1000  # cap `a ** b` exponent to bound big-int blowup

_SAFE_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.FloorDiv: operator.floordiv,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval(node: ast.AST) -> float | int:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"unsupported constant: {node.value!r}")
    if isinstance(node, ast.BinOp):
        op = _SAFE_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"unsupported operator: {type(node.op).__name__}")
        left = _safe_eval(node.left)
        right = _safe_eval(node.right)
        if isinstance(node.op, ast.Pow):
            # Guard against integer-exponentiation DoS, e.g. 10**100000000 (a
            # multi-GB big-int that hangs the worker). Cap the exponent, and
            # forbid raising an already-huge base to a further power (which would
            # let nesting like (10**1000)**1000 blow up).
            if isinstance(right, int) and abs(right) > _MAX_POW_EXPONENT:
                raise ValueError("exponent too large")
            if isinstance(left, int) and left.bit_length() > 64 and abs(right) > 1:
                raise ValueError("base too large for exponentiation")
        return op(left, right)
    if isinstance(node, ast.UnaryOp):
        op = _SAFE_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"unsupported unary op: {type(node.op).__name__}")
        return op(_safe_eval(node.operand))
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    raise ValueError(f"unsupported expression node: {type(node).__name__}")


def _calculate(args: dict[str, Any]) -> str:
    expr = str(args.get("expression", ""))
    if not expr:
        return "error: missing 'expression'"
    try:
        tree = ast.parse(expr, mode="eval")
        result = _safe_eval(tree)
    except (ValueError, SyntaxError, ZeroDivisionError) as e:
        return f"error: {e}"
    return str(result)


HANDLERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "now": _now,
    "calculate": _calculate,
}


# ---- image generation (async, gated on config) ----

IMAGINE_SPEC: dict = {
    "type": "function",
    "function": {
        "name": "imagine",
        "description": (
            "Generate an image from a text prompt. The generated image is "
            "shown to the user automatically — you do not need to render, link, "
            "or describe the raw image. Use this whenever the user asks you to "
            "create, draw, paint, generate, or imagine a picture or image."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "A detailed description of the image to generate.",
                },
                "size": {
                    "type": "string",
                    "description": 'Optional WxH, e.g. "1024x1024" or "512x768".',
                },
                "negative_prompt": {
                    "type": "string",
                    "description": "Optional things to avoid (ignored by some backends).",
                },
            },
            "required": ["prompt"],
        },
    },
}


async def _imagine(args: dict[str, Any], ctx: ToolContext | None) -> str:
    prompt = str(args.get("prompt", "")).strip()
    if not prompt:
        return "error: missing 'prompt'"
    try:
        data_url = await images.generate(
            prompt,
            size=args.get("size"),
            negative_prompt=args.get("negative_prompt"),
        )
    except images.ImageError as e:
        return f"error: {e}"
    if ctx is not None:
        ctx.images.append(data_url)
    return (
        f"Successfully generated an image for the prompt: {prompt!r}. "
        "The image is now displayed to the user in the chat."
    )


# ---- code interpreter (async, gated on config) ----

RUN_PYTHON_SPEC: dict = {
    "type": "function",
    "function": {
        "name": "run_python",
        "description": (
            "Execute Python 3 code in a sandbox and return its stdout/stderr. "
            "Use this to compute, analyse data, or verify logic. Print the "
            "results you want to see. The sandbox has no network access and is "
            "reset on every call (no state persists between calls). To show the "
            "user a chart or image, save it to a file (e.g. "
            "matplotlib.pyplot.savefig('plot.png')) — saved images are displayed "
            "automatically."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "The Python source to run."}
            },
            "required": ["code"],
        },
    },
}


def _format_exec(result: "code_exec.ExecResult") -> str:
    parts: list[str] = []
    if result.stdout.strip():
        parts.append("stdout:\n" + result.stdout.rstrip())
    if result.stderr.strip():
        parts.append("stderr:\n" + result.stderr.rstrip())
    if result.timed_out:
        parts.append(
            f"(execution exceeded the {settings.code_timeout_seconds:g}s time "
            "limit and was killed)"
        )
    elif result.exit_code not in (0, None):
        parts.append(f"(process exited with code {result.exit_code})")
    if result.images:
        n = len(result.images)
        parts.append(f"({n} image{'s' if n != 1 else ''} produced and shown to the user)")
    if not parts:
        parts.append("(no output)")
    return "\n\n".join(parts)


async def _run_python(args: dict[str, Any], ctx: ToolContext | None) -> str:
    code = str(args.get("code", ""))
    if not code.strip():
        return "error: missing 'code'"
    try:
        result = await code_exec.execute(code)
    except code_exec.CodeExecError as e:
        return f"error: {e}"
    if ctx is not None:
        ctx.images.extend(result.images)
    return _format_exec(result)


ASYNC_HANDLERS: dict[str, Callable[[dict[str, Any], "ToolContext | None"], Awaitable[str]]] = {
    "imagine": _imagine,
    "run_python": _run_python,
}


def builtin_tool_specs() -> list[dict]:
    """OpenAI tool schemas for every built-in available right now. `imagine`
    and `run_python` only appear when their backends are configured."""
    specs = list(TOOL_SPECS)
    if settings.image_backend:
        specs.append(IMAGINE_SPEC)
    if code_exec._resolve_backend():
        specs.append(RUN_PYTHON_SPEC)
    return specs


def run_tool(name: str, args: dict[str, Any]) -> str:
    handler = HANDLERS.get(name)
    if handler is None:
        return f"error: unknown tool {name!r}"
    try:
        return handler(args)
    except Exception as e:
        return f"error: {e}"


async def run_tool_async(
    name: str, args: dict[str, Any], ctx: ToolContext | None = None
) -> str:
    """Dispatch a built-in tool. Async tools (e.g. `imagine`) run here;
    everything else falls through to the synchronous registry."""
    async_handler = ASYNC_HANDLERS.get(name)
    if async_handler is not None:
        try:
            return await async_handler(args, ctx)
        except Exception as e:
            return f"error: {e}"
    return run_tool(name, args)
