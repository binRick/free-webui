"""Optional S3-compatible object store for media blobs.

When ``s3_bucket`` is configured, file bytes live in an S3 bucket (key = file id)
instead of the ``files.data`` column; the DB row stays as the canonical index
(access control, GC enumeration, size for inline budgeting) with
``storage = 's3'`` and an empty ``data`` blob. Works with AWS S3, MinIO, Ceph,
Backblaze B2, and any SigV4 endpoint.

Signing is AWS Signature V4, implemented with the standard library (``hmac`` +
``hashlib``) so this adds no dependency. The endpoint is operator-configured
(not user-supplied), so — unlike the MCP/OpenAPI tool fetchers — it needs no
SSRF guard.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import quote, urlsplit

import httpx

from .config import settings

_SERVICE = "s3"
_ALGORITHM = "AWS4-HMAC-SHA256"
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret: str, date_stamp: str, region: str, service: str) -> bytes:
    k = _hmac(("AWS4" + secret).encode("utf-8"), date_stamp)
    k = _hmac(k, region)
    k = _hmac(k, service)
    return _hmac(k, "aws4_request")


def sigv4_signature(
    secret: str,
    method: str,
    canonical_uri: str,
    headers: dict[str, str],
    payload_hash: str,
    amz_date: str,
    region: str,
    service: str = _SERVICE,
    canonical_querystring: str = "",
) -> str:
    """Pure SigV4 signature (hex). Factored out so it can be unit-tested against
    AWS's documented example vectors. ``headers`` are the headers to sign (their
    names are lower-cased and the set becomes SignedHeaders, alphabetical)."""
    lower = {k.lower(): str(v).strip() for k, v in headers.items()}
    signed_headers = ";".join(sorted(lower))
    canonical_headers = "".join(f"{k}:{lower[k]}\n" for k in sorted(lower))
    canonical_request = "\n".join(
        [method, canonical_uri, canonical_querystring, canonical_headers, signed_headers, payload_hash]
    )
    date_stamp = amz_date[:8]
    scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [_ALGORITHM, amz_date, scope, _sha256_hex(canonical_request.encode("utf-8"))]
    )
    key = _signing_key(secret, date_stamp, region, service)
    return hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()


class S3Store:
    def __init__(
        self,
        *,
        endpoint: str,
        region: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        path_style: bool = True,
        prefix: str = "",
        client: httpx.AsyncClient | None = None,
    ):
        self.endpoint = endpoint.rstrip("/")
        self.region = region
        self.bucket = bucket
        self.access_key = access_key
        self.secret_key = secret_key
        self.path_style = path_style
        self.prefix = prefix
        self._own_client = client is None
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))

    async def aclose(self) -> None:
        if self._own_client:
            await self.client.aclose()

    # ---- request construction ----

    def _authority(self) -> tuple[str, str]:
        """Return (scheme, host) where host matches the Host header httpx will
        actually send. httpx drops a port that equals the scheme default, so we
        must sign the same — otherwise SigV4 fails with SignatureDoesNotMatch
        whenever the endpoint carries an explicit :443/:80."""
        parsed = urlsplit(self.endpoint)
        hostname = parsed.hostname or ""
        if ":" in hostname:  # IPv6 literal — Host header brackets it
            hostname = f"[{hostname}]"
        default = {"https": 443, "http": 80}.get(parsed.scheme)
        port = parsed.port
        host = hostname if port is None or port == default else f"{hostname}:{port}"
        return parsed.scheme, host

    def _url_and_host(self, key: str) -> tuple[str, str, str]:
        """Return (url, host, canonical_uri) for an object key. S3 canonical URIs
        are single-encoded with '/' preserved."""
        scheme, base_host = self._authority()
        enc_key = quote(key, safe="/~_-.")
        if self.path_style:
            host = base_host
            canonical_uri = f"/{self.bucket}/{enc_key}"
        else:
            host = f"{self.bucket}.{base_host}"
            canonical_uri = f"/{enc_key}"
        return f"{scheme}://{host}{canonical_uri}", host, canonical_uri

    def _signed_headers(
        self, method: str, key: str, payload: bytes, content_type: str | None = None
    ) -> tuple[str, dict[str, str]]:
        url, host, canonical_uri = self._url_and_host(key)
        amz_date = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        payload_hash = _sha256_hex(payload)
        # Only host + the x-amz-* headers are signed (Content-Type is sent
        # unsigned, which SigV4 permits — S3 still stores it on the object).
        to_sign = {
            "host": host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        sig = sigv4_signature(
            self.secret_key, method, canonical_uri, to_sign, payload_hash, amz_date, self.region
        )
        scope = f"{amz_date[:8]}/{self.region}/{_SERVICE}/aws4_request"
        signed_headers = ";".join(sorted(to_sign))
        headers = {
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
            "Authorization": (
                f"{_ALGORITHM} Credential={self.access_key}/{scope}, "
                f"SignedHeaders={signed_headers}, Signature={sig}"
            ),
        }
        if content_type:
            headers["Content-Type"] = content_type
        return url, headers

    def _key(self, file_id: str) -> str:
        return f"{self.prefix}{file_id}"

    # ---- operations ----

    async def put(self, file_id: str, data: bytes, content_type: str) -> None:
        url, headers = self._signed_headers("PUT", self._key(file_id), data, content_type)
        r = await self.client.put(url, content=data, headers=headers)
        if r.status_code >= 300:
            raise RuntimeError(f"s3 put failed {r.status_code}: {r.text[:200]}")

    async def get(self, file_id: str) -> bytes | None:
        url, headers = self._signed_headers("GET", self._key(file_id), b"")
        r = await self.client.get(url, headers=headers)
        if r.status_code == 404:
            return None
        if r.status_code >= 300:
            raise RuntimeError(f"s3 get failed {r.status_code}: {r.text[:200]}")
        return r.content

    async def delete(self, file_id: str) -> None:
        url, headers = self._signed_headers("DELETE", self._key(file_id), b"")
        r = await self.client.delete(url, headers=headers)
        # 204 (deleted) and 404 (already gone) are both fine.
        if r.status_code not in (200, 202, 204, 404):
            raise RuntimeError(f"s3 delete failed {r.status_code}: {r.text[:200]}")

    async def ensure_bucket(self) -> None:
        """Create the bucket if absent (idempotent). Mainly for tests/dev — in
        production the operator provisions the bucket."""
        scheme, base_host = self._authority()
        host = base_host if self.path_style else f"{self.bucket}.{base_host}"
        canonical_uri = f"/{self.bucket}" if self.path_style else "/"
        url = f"{scheme}://{host}{canonical_uri}"
        # AWS requires a LocationConstraint body for any region != us-east-1
        # (MinIO/Ceph ignore it). Sign the real body hash.
        if self.region == "us-east-1":
            body = b""
        else:
            body = (
                '<CreateBucketConfiguration xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                f"<LocationConstraint>{self.region}</LocationConstraint>"
                "</CreateBucketConfiguration>"
            ).encode("utf-8")
        payload_hash = _sha256_hex(body)
        amz_date = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        to_sign = {"host": host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
        sig = sigv4_signature(
            self.secret_key, "PUT", canonical_uri, to_sign, payload_hash, amz_date, self.region
        )
        scope = f"{amz_date[:8]}/{self.region}/{_SERVICE}/aws4_request"
        headers = {
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
            "Authorization": (
                f"{_ALGORITHM} Credential={self.access_key}/{scope}, "
                f"SignedHeaders=host;x-amz-content-sha256;x-amz-date, Signature={sig}"
            ),
        }
        r = await self.client.put(url, content=body, headers=headers)
        # 200 created; 409 BucketAlreadyOwnedByYou / BucketAlreadyExists.
        if r.status_code not in (200, 409):
            raise RuntimeError(f"s3 ensure_bucket failed {r.status_code}: {r.text[:200]}")


def make_object_store(client: httpx.AsyncClient | None = None) -> S3Store | None:
    """Build the configured object store, or None when S3 is not configured
    (the default: blobs stay in the ``files.data`` column)."""
    if not settings.s3_bucket:
        return None
    return S3Store(
        endpoint=settings.s3_endpoint_url or "https://s3.amazonaws.com",
        region=settings.s3_region,
        bucket=settings.s3_bucket,
        access_key=settings.s3_access_key_id,
        secret_key=settings.s3_secret_access_key,
        path_style=settings.s3_force_path_style,
        prefix=settings.s3_prefix,
        client=client,
    )
