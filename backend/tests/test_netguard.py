"""SSRF guard (netguard.validate_url) + the MCP probe wiring."""
import socket

import pytest


def _cfg(monkeypatch, *, protection=True, block_private=True, allow=None):
    from app import netguard
    monkeypatch.setattr(netguard.settings, "ssrf_protection", protection)
    monkeypatch.setattr(netguard.settings, "ssrf_block_private", block_private)
    monkeypatch.setattr(netguard.settings, "ssrf_allow_hosts", allow or [])
    return netguard


def test_blocks_cloud_metadata(monkeypatch):
    ng = _cfg(monkeypatch)
    with pytest.raises(ng.BlockedURLError):
        ng.validate_url("http://169.254.169.254/latest/meta-data/")


def test_blocks_loopback_and_private(monkeypatch):
    ng = _cfg(monkeypatch)
    for url in (
        "http://127.0.0.1:11434/v1",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://[::1]/",
    ):
        with pytest.raises(ng.BlockedURLError):
            ng.validate_url(url)


def test_allows_public_literals(monkeypatch):
    ng = _cfg(monkeypatch)
    ng.validate_url("http://1.1.1.1/")
    ng.validate_url("https://8.8.8.8/path?q=1")


def test_loopback_allowed_when_block_private_off(monkeypatch):
    ng = _cfg(monkeypatch, block_private=False)
    ng.validate_url("http://127.0.0.1:11434/v1")  # now allowed
    # ...but link-local / metadata is blocked regardless of block_private.
    with pytest.raises(ng.BlockedURLError):
        ng.validate_url("http://169.254.169.254/")


def test_allowlist_bypasses_block(monkeypatch):
    ng = _cfg(monkeypatch, allow=["127.0.0.1"])
    ng.validate_url("http://127.0.0.1:8000/")
    _cfg(monkeypatch, allow=["10.0.0.0/8"])
    ng.validate_url("http://10.1.2.3/")


def test_rejects_non_http_scheme(monkeypatch):
    ng = _cfg(monkeypatch)
    for url in ("ftp://127.0.0.1/", "file:///etc/passwd", "gopher://1.1.1.1/"):
        with pytest.raises(ng.BlockedURLError):
            ng.validate_url(url)


def test_unresolvable_host_is_allowed(monkeypatch):
    ng = _cfg(monkeypatch)

    def boom(*a, **k):
        raise socket.gaierror("name resolution failed")

    monkeypatch.setattr(ng.socket, "getaddrinfo", boom)
    ng.validate_url("http://unresolvable.example/")  # no raise — can't connect anyway


def test_hostname_resolving_to_private_is_blocked(monkeypatch):
    ng = _cfg(monkeypatch)
    monkeypatch.setattr(
        ng.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.0.0.7", 0))]
    )
    with pytest.raises(ng.BlockedURLError):
        ng.validate_url("http://internal.corp.example/admin")


def test_disabled_guard_is_noop(monkeypatch):
    ng = _cfg(monkeypatch, protection=False)
    ng.validate_url("http://169.254.169.254/")  # no raise when disabled


async def test_mcp_probe_blocks_metadata_url(client, monkeypatch):
    """End-to-end: probing an MCP server pointed at link-local metadata is
    refused with 400 before any outbound request."""
    from app import netguard

    monkeypatch.setattr(netguard.settings, "ssrf_protection", True)
    monkeypatch.setattr(netguard.settings, "ssrf_block_private", True)
    monkeypatch.setattr(netguard.settings, "ssrf_allow_hosts", [])

    await client.post(
        "/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"}
    )
    sid = (
        await client.post(
            "/api/mcp_servers",
            json={"name": "meta", "url": "http://169.254.169.254/rpc"},
        )
    ).json()["id"]
    r = await client.post(f"/api/mcp_servers/{sid}/probe")
    assert r.status_code == 400
    assert "blocked" in r.json()["detail"].lower()
