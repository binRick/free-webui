"""SSRF guard for outbound URLs that originate from user or operator input.

The MCP feature (mcp.py) lets any authenticated user store an arbitrary URL
that the server then POSTs to, and the image backends (images.py) fetch result
URLs. Without a guard those become a server-side request forgery vector: a user
could aim the server at cloud metadata (169.254.169.254), loopback, or internal
RFC1918 hosts and read the responses.

`validate_url()` parses the URL, requires an http(s) scheme, resolves the host,
and refuses link-local / multicast / reserved / unspecified ranges always, plus
loopback + private ranges when `ssrf_block_private` is set. Operators can
allowlist specific hosts or CIDRs (`ssrf_allow_hosts`).

Resolution failures are allowed through: an unresolvable host cannot be
connected to, so it is not an SSRF target — the real request will simply fail.

NOTE (DNS rebinding): this validates at *check* time. A determined attacker who
controls DNS could rebind a hostname to a private address between this check and
the actual connection. Pinning the resolved IP into the transport closes that;
it is a deliberate follow-up. The common cases (a literal private IP, or a
hostname whose A/AAAA records resolve to a private range) are covered here.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

from .config import settings

_ALLOWED_SCHEMES = {"http", "https"}

_IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class BlockedURLError(RuntimeError):
    """Raised when a URL is refused by the SSRF guard.

    Subclasses RuntimeError so existing ``except RuntimeError`` handlers
    (mcp.py) treat a blocked URL as an ordinary upstream failure.
    """


def _allowlisted(host: str, ips: list[_IpAddress]) -> bool:
    for raw in settings.ssrf_allow_hosts:
        entry = (raw or "").strip()
        if not entry:
            continue
        if entry.lower() == host.lower():
            return True
        try:
            net = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            continue
        if any(ip in net for ip in ips):
            return True
    return False


def _blocked_ip(ip: _IpAddress) -> bool:
    # Unwrap IPv4-mapped IPv6 (::ffff:a.b.c.d) and re-check the inner address.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        return _blocked_ip(mapped)
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified or ip.is_link_local:
        return True
    if settings.ssrf_block_private and (ip.is_private or ip.is_loopback):
        return True
    return False


def _resolve(host: str) -> list[_IpAddress]:
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    out: list[_IpAddress] = []
    for info in infos:
        addr = info[4][0].split("%", 1)[0]  # strip IPv6 scope id (fe80::1%eth0)
        try:
            out.append(ipaddress.ip_address(addr))
        except ValueError:
            continue
    return out


def validate_url(url: str) -> None:
    """Raise BlockedURLError if `url` is not safe to fetch. No-op when the
    guard is disabled or the host cannot be resolved."""
    if not settings.ssrf_protection:
        return
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise BlockedURLError(f"blocked url scheme {scheme!r} (only http/https allowed)")
    host = parsed.hostname
    if not host:
        raise BlockedURLError("blocked url: missing host")

    # A bare IP literal needs no DNS.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _allowlisted(host, [literal]):
            return
        if _blocked_ip(literal):
            raise BlockedURLError(
                f"blocked url: {host} is a disallowed address "
                "(set FREE_WEBUI_SSRF_BLOCK_PRIVATE=false or add it to "
                "FREE_WEBUI_SSRF_ALLOW_HOSTS to allow)"
            )
        return

    try:
        ips = _resolve(host)
    except (socket.gaierror, UnicodeError, OSError):
        return  # cannot resolve -> cannot connect -> not an SSRF target
    if not ips or _allowlisted(host, ips):
        return
    for ip in ips:
        if _blocked_ip(ip):
            raise BlockedURLError(
                f"blocked url: {host} resolves to disallowed address {ip} "
                "(set FREE_WEBUI_SSRF_BLOCK_PRIVATE=false or add the host to "
                "FREE_WEBUI_SSRF_ALLOW_HOSTS to allow)"
            )


async def check_url(url: str) -> None:
    """Async wrapper: runs the (blocking) DNS resolution off the event loop."""
    await asyncio.to_thread(validate_url, url)
