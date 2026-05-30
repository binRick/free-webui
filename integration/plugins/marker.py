"""Integration-harness plugin.

A deterministic `outlet` that appends a fixed marker to the persisted assistant
text. This lets the suite assert the plugin pipeline actually ran end-to-end
against a real model WITHOUT depending on anything the (non-deterministic) model
said — the marker is model-independent, and because outlets only rewrite the
*persisted* text, the marker must be absent from the live stream and present on
reload.
"""

MARKER = "<!-- via-plugin -->"


async def outlet(text: str, ctx) -> str:
    return f"{text}\n\n{MARKER}"
