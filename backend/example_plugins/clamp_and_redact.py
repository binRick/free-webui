"""Example free-webui plugin.

inlet:  clamp the sampling temperature so no chat can request a wildly high one.
outlet: redact email addresses from the assistant's reply before it is stored.

To try it, point the backend at this directory:

    export FREE_WEBUI_PLUGINS_DIR=backend/example_plugins

Plugins are TRUSTED, in-process operator code — only load files you wrote or
audited. Hooks must be `async`; do blocking work via `asyncio.to_thread`.
"""
import re

# Lower PRIORITY runs first on inlet; outlet runs in reverse order.
PRIORITY = 10

_MAX_TEMPERATURE = 1.2
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


async def inlet(body: dict, ctx) -> dict:
    temperature = body.get("temperature")
    if isinstance(temperature, (int, float)) and temperature > _MAX_TEMPERATURE:
        body["temperature"] = _MAX_TEMPERATURE
    return body


async def outlet(text: str, ctx) -> str:
    return _EMAIL.sub("[redacted]", text)
