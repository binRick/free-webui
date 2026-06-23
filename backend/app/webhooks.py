"""Outgoing operator webhooks (currently new-user signups).

POSTs a JSON payload to ``FREE_WEBUI_WEBHOOK_URL`` when set. The payload carries a
human-readable ``text`` (Slack incoming-webhook field) and ``content`` (Discord
field) so either renders it out of the box, plus structured fields for generic
consumers. Best-effort: a failure or timeout is logged and swallowed — it never
breaks the signup that triggered it. The URL is operator config (trusted, like
the upstream/embedding URLs), so it is not SSRF-guarded.
"""
import logging

import httpx

from .config import settings

log = logging.getLogger("free_webui.webhooks")


async def notify_signup(username: str, role: str, via: str) -> None:
    """Fire the signup webhook (no-op when unconfigured). Never raises."""
    url = settings.webhook_url
    if not url:
        return
    text = f"New {role} signed up on {settings.instance_name}: {username} (via {via})"
    payload = {
        "event": "user.signup",
        "text": text,        # Slack incoming-webhook
        "content": text,     # Discord incoming-webhook
        "username": username,
        "role": role,
        "via": via,
        "instance": settings.instance_name,
    }
    try:
        async with httpx.AsyncClient(timeout=settings.webhook_timeout_seconds) as client:
            await client.post(url, json=payload)
    except httpx.HTTPError:
        log.warning("signup webhook POST failed", exc_info=True)
