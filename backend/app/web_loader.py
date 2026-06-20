"""Fetch a URL and turn it into ingestible text for RAG.

Lets a user paste a link (web page, PDF, plain text / code file) and ground a
chat or knowledge base in its contents, like an uploaded document. The fetch is
SSRF-guarded (netguard, per redirect hop), size-capped (rag_max_upload_bytes),
and dependency-free for HTML (stdlib html.parser strips tags + script/style).

Returns ``(label, mime, text)``: a readable label (the page <title> or the URL
basename), the source content-type (for display), and the extracted text. The
caller chunks + embeds it via ``rag.prepare_text`` exactly like a file upload.
"""
from __future__ import annotations

import asyncio
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import HTTPException

from .config import settings
from .netguard import BlockedURLError, check_url
from .rag import extract_text

_MAX_REDIRECTS = 5
_UA = "free-webui/url-loader"
# Tags whose text content is never page copy (scripts, styling, inline SVG, …).
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head"}
# Block- and cell-level tags that should force a line break so adjacent values
# (table cells, list items, headings, definition terms) don't run together.
_BREAK_TAGS = {
    "p", "br", "div", "section", "article", "header", "footer", "li", "tr",
    "td", "th", "dd", "dt", "caption", "figcaption",
    "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "ul", "ol", "table",
}
# Content-types we trust to be HTML outright.
_HTML_MIMES = {"text/html", "application/xhtml+xml"}
# Ambiguous/generic content-types where we still sniff the body for HTML (many
# servers mislabel pages as text/plain or application/octet-stream).
_SNIFF_MIMES = {"", "text/plain", "application/octet-stream", "application/xml", "text/xml"}


class _TextExtractor(HTMLParser):
    """Collect visible text + the document title from an HTML byte string."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._title: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._title_captured = False  # only the first non-empty <title> is the label

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in _BREAK_TAGS:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag in _BREAK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            if self._title:
                self._title_captured = True
            self._in_title = False
        if tag in _BREAK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        # Title text never reaches the body, and only the first title is the label.
        if self._in_title:
            if not self._title_captured:
                self._title.append(data)
            return
        if self._skip_depth:
            return
        self._parts.append(data)

    def title(self) -> str:
        joined = re.sub(r"<[^>]+>", "", "".join(self._title))  # drop any residual tags
        return re.sub(r"\s+", " ", joined).strip()

    def text(self) -> str:
        raw = "".join(self._parts).replace("\xa0", " ")
        # collapse intra-line whitespace, then squeeze blank-line runs to one.
        # split on '\n' only (not splitlines, which also breaks on U+2028/2029).
        lines = [re.sub(r"[ \t\f\v]+", " ", ln).strip() for ln in raw.split("\n")]
        out: list[str] = []
        blanks = 0
        for ln in lines:
            if ln:
                blanks = 0
                out.append(ln)
            else:
                blanks += 1
                if blanks == 1:
                    out.append("")
        return "\n".join(out).strip()


def _html_to_text(data: bytes) -> tuple[str, str]:
    parser = _TextExtractor()
    parser.feed(data.decode("utf-8", errors="replace"))
    parser.close()
    return parser.title(), parser.text()


def _looks_like_html(data: bytes) -> bool:
    head = data[:512].lstrip(b"\xef\xbb\xbf").lstrip().lower()  # drop UTF-8 BOM
    return (
        head.startswith(b"<!doctype html")
        or head.startswith(b"<html")
        or b"<html" in head  # tolerate a leading comment/prolog before <html
        or b"<head" in head
    )


def _label_from_url(url: str) -> str:
    parsed = urlparse(url)
    path = (parsed.path or "").rstrip("/")
    base = path.rsplit("/", 1)[-1] if path else ""
    return base or parsed.netloc or url


async def _read_capped(resp: httpx.Response, cap: int) -> bytes:
    total = 0
    buf: list[bytes] = []
    async for chunk in resp.aiter_bytes():
        total += len(chunk)
        if total > cap:
            raise HTTPException(
                status_code=413, detail=f"remote document too large (max {cap} bytes)"
            )
        buf.append(chunk)
    return b"".join(buf)


async def fetch_url(url: str) -> tuple[str, str, str]:
    """Fetch ``url`` and return ``(label, mime, text)``. Raises HTTPException on
    a blocked/unreachable URL, an HTTP error, an oversize body, empty text, or a
    total-time overrun.

    Redirects are followed manually (max 5) so every hop is SSRF-checked — auto
    following inside httpx would skip the guard and reopen the rebinding hole.
    The whole fetch (all hops) is bounded by an absolute deadline so a slow-
    trickle host can't pin a connection past the per-read timeout.
    """
    current = (url or "").strip()
    if not current:
        raise HTTPException(status_code=400, detail="empty url")
    # Absolute wall-clock budget across every redirect hop (the per-read httpx
    # timeout resets each chunk, so it is not a total-transfer cap on its own).
    budget = max(1.0, settings.url_fetch_timeout_seconds) * (_MAX_REDIRECTS + 1)
    try:
        async with asyncio.timeout(budget):
            return await _fetch(current)
    except TimeoutError:
        raise HTTPException(status_code=504, detail="timed out fetching url")


async def _fetch(current: str) -> tuple[str, str, str]:
    cap = settings.rag_max_upload_bytes
    seen = {current}
    data = b""
    ctype = ""
    final_url = current
    headers = {"user-agent": _UA, "accept": "text/html,application/xhtml+xml,*/*"}

    # Own client (matching mcp/web_search), so tests intercept it by monkeypatching
    # httpx.AsyncClient. trust_env=False so an ambient HTTP(S)_PROXY can't route
    # the request around the SSRF IP check; follow_redirects off so we re-check.
    async with httpx.AsyncClient(
        timeout=settings.url_fetch_timeout_seconds,
        headers=headers,
        trust_env=False,
        follow_redirects=False,
    ) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            try:
                await check_url(current)
            except BlockedURLError as e:
                raise HTTPException(status_code=400, detail=str(e))
            try:
                async with client.stream("GET", current) as resp:
                    if resp.has_redirect_location:  # 3xx WITH a Location header
                        nxt = urljoin(current, resp.headers["location"])
                        if nxt in seen:
                            raise HTTPException(status_code=400, detail="redirect loop")
                        seen.add(nxt)
                        current = nxt
                        continue
                    if resp.status_code >= 400:
                        raise HTTPException(
                            status_code=502, detail=f"fetch failed: HTTP {resp.status_code}"
                        )
                    ctype = resp.headers.get("content-type", "")
                    final_url = current
                    data = await _read_capped(resp, cap)
                    break
            except httpx.HTTPError as e:
                raise HTTPException(status_code=502, detail=f"could not fetch url: {e}")
        else:
            raise HTTPException(status_code=400, detail="too many redirects")

    mime = ctype.split(";", 1)[0].strip().lower()
    if mime in _HTML_MIMES or (mime in _SNIFF_MIMES and _looks_like_html(data)):
        title, text = _html_to_text(data)
        if not text.strip():
            raise HTTPException(status_code=422, detail="no readable text on that page")
        return (title or _label_from_url(final_url)), "text/html", text

    # Non-HTML: reuse the file extractor (PDF / text / code), raising 415 on binary.
    label = _label_from_url(final_url)
    text = extract_text(label, mime or None, data)
    if not text.strip():
        raise HTTPException(status_code=422, detail="no readable text at that url")
    return label, (mime or "text/plain"), text
