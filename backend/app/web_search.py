"""SearXNG-style web search client. Used to prepend live results as a
system message before calling the LLM."""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .auth import current_user
from .config import settings

router = APIRouter(prefix="/api/web_search", tags=["web_search"])


class WebSearchStatus(BaseModel):
    available: bool
    url: str | None = None


@router.get("/status", response_model=WebSearchStatus, dependencies=[Depends(current_user)])
async def status_endpoint() -> WebSearchStatus:
    if not settings.searxng_url:
        return WebSearchStatus(available=False)
    return WebSearchStatus(available=True, url=settings.searxng_url)


async def search(query: str) -> list[dict]:
    """Hit the configured SearXNG instance and return a list of result
    dicts: {title, url, content}. Empty list on any failure — searches must
    never break the chat flow."""
    if not (settings.searxng_url and query.strip()):
        return []
    base = settings.searxng_url.rstrip("/")
    url = base + "/search"
    try:
        async with httpx.AsyncClient(
            timeout=settings.web_search_timeout_seconds,
            headers={"Accept": "application/json"},
        ) as c:
            r = await c.get(url, params={"q": query, "format": "json"})
        if r.status_code >= 400:
            return []
        body = r.json()
    except (httpx.HTTPError, ValueError):
        return []
    results = body.get("results") or []
    out: list[dict] = []
    for item in results[: settings.web_search_top_k]:
        out.append(
            {
                "title": item.get("title", "") or "",
                "url": item.get("url", "") or "",
                "content": item.get("content", "") or "",
            }
        )
    return out


def format_context(results: list[dict]) -> str | None:
    if not results:
        return None
    blocks = []
    for r in results:
        snippet = (r["content"] or "").strip()
        blocks.append(
            f'### {r["title"]}\n{r["url"]}\n{snippet}'.strip()
        )
    body = "\n\n".join(blocks)
    return (
        "You have access to the following live web search results. Use them "
        "when answering, and cite the URLs you draw from. Say so if none of "
        "them are relevant.\n\n" + body
    )
