"""Built-in safe tools exposed to the LLM via OpenAI function-calling.

Adding a tool requires:
  1. A handler function that takes a dict of args and returns a string.
  2. An entry in TOOL_SPECS (OpenAI tool schema).
  3. An entry in HANDLERS keyed by tool name.
"""
from __future__ import annotations

import ast
import datetime
import operator
from typing import Any, Callable

# OpenAI function/tool schemas.
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
        return op(_safe_eval(node.left), _safe_eval(node.right))
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


def run_tool(name: str, args: dict[str, Any]) -> str:
    handler = HANDLERS.get(name)
    if handler is None:
        return f"error: unknown tool {name!r}"
    try:
        return handler(args)
    except Exception as e:
        return f"error: {e}"
