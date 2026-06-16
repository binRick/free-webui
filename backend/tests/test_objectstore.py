"""S3 object store: SigV4 correctness + DB↔S3 round-trip through the file layer."""
import base64
import os

import httpx
import pytest

# NOTE: the `client` fixture deletes + re-imports every app.* module per test,
# so app-using tests must import `app.files` INSIDE the test (the fresh module
# the running app actually uses) before monkeypatching its `_STORE` singleton.
from app.objectstore import S3Store, sigv4_signature

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def test_sigv4_matches_aws_documented_vector():
    """Reproduce the signature from AWS's SigV4 "GET Object" worked example.
    https://docs.aws.amazon.com/AmazonS3/latest/API/sig-v4-header-based-auth.html
    """
    empty = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    sig = sigv4_signature(
        secret="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        method="GET",
        canonical_uri="/test.txt",
        headers={
            "host": "examplebucket.s3.amazonaws.com",
            "range": "bytes=0-9",
            "x-amz-content-sha256": empty,
            "x-amz-date": "20130524T000000Z",
        },
        payload_hash=empty,
        amz_date="20130524T000000Z",
        region="us-east-1",
    )
    assert sig == "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"


def test_signed_host_strips_default_port():
    """The signed Host must match the wire Host httpx sends, which drops a
    default port — otherwise SigV4 fails with SignatureDoesNotMatch."""
    https = S3Store(endpoint="https://s3.amazonaws.com:443", region="us-east-1",
                    bucket="b", access_key="ak", secret_key="sk", path_style=True)
    _url, host, _uri = https._url_and_host("k")
    assert host == "s3.amazonaws.com"  # :443 stripped

    http = S3Store(endpoint="http://minio:80", region="us-east-1",
                   bucket="b", access_key="ak", secret_key="sk", path_style=True)
    assert http._url_and_host("k")[1] == "minio"  # :80 stripped

    # a non-default port is preserved (it IS on the wire Host)
    keep = S3Store(endpoint="http://minio:9000", region="us-east-1",
                   bucket="b", access_key="ak", secret_key="sk", path_style=True)
    assert keep._url_and_host("k")[1] == "minio:9000"

    # virtual-host style prefixes the bucket onto the normalized host
    vh = S3Store(endpoint="https://s3.amazonaws.com:443", region="us-east-1",
                 bucket="media", access_key="ak", secret_key="sk", path_style=False)
    assert vh._url_and_host("k")[1] == "media.s3.amazonaws.com"


async def test_clone_does_not_copy_foreign_conversation_blob(client):
    """clone_file_refs must only copy blobs from the source conversation. A
    forged ref to another conversation's blob is left untouched (no re-owned
    copy), closing the cross-tenant exfil-via-clone hole."""
    from app import files
    from app.main import app

    await _signup(client)
    me = (await client.get("/api/auth/me")).json()["id"]
    conv_a = (await client.post("/api/conversations", json={})).json()["id"]
    conv_b = (await client.post("/api/conversations", json={})).json()["id"]
    conv_c = (await client.post("/api/conversations", json={})).json()["id"]  # clone target
    db = app.state.db

    # a blob that belongs to conversation A
    ref = await files.store_data_url(
        db, me, conv_a, "data:image/png;base64," + base64.b64encode(PNG).decode()
    )
    await db.commit()
    fid_v = ref.rsplit("/", 1)[-1]
    forged = f'[{{"type":"image_url","image_url":{{"url":"/api/files/{fid_v}"}}}}]'

    # Cloning into a NEW conversation while sourcing from conv_b (which does NOT
    # own fid_v) must not copy the blob: the ref is left verbatim, no new row.
    before = (await (await db.execute("SELECT COUNT(*) FROM files")).fetchone())[0]
    out = await files.clone_file_refs(db, me, "new-cid", conv_b, forged)
    assert out == forged  # ref unchanged -> not re-owned
    after = (await (await db.execute("SELECT COUNT(*) FROM files")).fetchone())[0]
    assert after == before  # no copy created

    # Sourcing from the true owner (conv_a) DOES copy + rewrite into conv_c.
    out2 = await files.clone_file_refs(db, me, conv_c, conv_a, forged)
    assert out2 != forged and "/api/files/" in out2
    assert (await (await db.execute("SELECT COUNT(*) FROM files")).fetchone())[0] == before + 1


def _fake_s3():
    """An in-memory path-style S3 over httpx.MockTransport. Returns (store,
    bucket_dict). Verifies every mutating call carried a SigV4 Authorization."""
    objects: dict[str, bytes] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        # /{bucket}/{key}
        key = req.url.path.split("/", 2)[2] if req.url.path.count("/") >= 2 else ""
        if req.method in ("PUT", "GET", "DELETE"):
            assert req.headers.get("authorization", "").startswith("AWS4-HMAC-SHA256")
        if req.method == "PUT":
            objects[key] = req.content
            return httpx.Response(200)
        if req.method == "GET":
            if key not in objects:
                return httpx.Response(404)
            return httpx.Response(200, content=objects[key])
        if req.method == "DELETE":
            objects.pop(key, None)
            return httpx.Response(204)
        return httpx.Response(400)

    store = S3Store(
        endpoint="http://minio.test",
        region="us-east-1",
        bucket="media",
        access_key="ak",
        secret_key="sk",
        path_style=True,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://minio.test"),
    )
    return store, objects


async def _signup(client):
    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})


async def test_s3_round_trip_store_serve_delete(client, monkeypatch):
    """With S3 configured, a stored blob lands in the bucket (not the DB), serves
    back through /api/files/{id}, and is removed when its conversation is."""
    from app import files
    from app.main import app

    await _signup(client)
    me = (await client.get("/api/auth/me")).json()["id"]
    cid = (await client.post("/api/conversations", json={})).json()["id"]

    store, bucket = _fake_s3()
    monkeypatch.setattr(files, "_STORE", store)

    data_url = "data:image/png;base64," + base64.b64encode(PNG).decode()
    ref = await files.store_data_url(app.state.db, me, cid, data_url)
    await app.state.db.commit()
    fid = ref.rsplit("/", 1)[-1]

    # The DB row is a pure index: storage='s3', empty data, real size.
    row = await (
        await app.state.db.execute(
            "SELECT data, size, storage FROM files WHERE id = ?", (fid,)
        )
    ).fetchone()
    assert row[2] == "s3" and bytes(row[0]) == b"" and row[1] == len(PNG)
    # The bytes really live in the (fake) bucket.
    assert bucket[fid] == PNG

    # Served back from S3 through the auth-gated endpoint.
    r = await client.get(ref)
    assert r.status_code == 200 and r.content == PNG

    # Deleting the conversation removes the S3 object too (cascade can't reach S3).
    assert (await client.delete(f"/api/conversations/{cid}")).status_code == 204
    assert fid not in bucket


async def test_s3_get_404_when_object_missing(client, monkeypatch):
    """An index row whose object vanished from the bucket serves a 404, not a
    500 or empty body."""
    from app import files
    from app.main import app

    await _signup(client)
    me = (await client.get("/api/auth/me")).json()["id"]
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    store, bucket = _fake_s3()
    monkeypatch.setattr(files, "_STORE", store)

    ref = await files.store_data_url(
        app.state.db, me, cid, "data:image/png;base64," + base64.b64encode(PNG).decode()
    )
    await app.state.db.commit()
    bucket.clear()  # object lost out from under the index row
    assert (await client.get(ref)).status_code == 404


async def test_s3_access_control_precedes_fetch(client, monkeypatch):
    """A non-owner is rejected (404) without the object store being touched."""
    from app import files
    from app.main import app

    await _signup(client)  # alice (admin)
    me = (await client.get("/api/auth/me")).json()["id"]
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    store, bucket = _fake_s3()
    monkeypatch.setattr(files, "_STORE", store)
    ref = await files.store_data_url(
        app.state.db, me, cid, "data:image/png;base64," + base64.b64encode(PNG).decode()
    )
    await app.state.db.commit()

    # a second, non-admin user who owns neither the file nor the conversation
    await client.post("/api/admin/users", json={"username": "bob", "password": "passpass", "role": "user"})
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"username": "bob", "password": "passpass"})
    assert (await client.get(ref)).status_code == 404


# ---- optional: real MinIO integration (gated; set FREE_WEBUI_TEST_S3=1) ----

@pytest.mark.skipif(not os.getenv("FREE_WEBUI_TEST_S3"), reason="set FREE_WEBUI_TEST_S3 + a MinIO")
async def test_real_s3_round_trip():
    """Round-trip against a real SigV4 endpoint (MinIO). Proves the signing is
    accepted by an actual server, not just our in-memory double."""
    store = S3Store(
        endpoint=os.environ.get("FREE_WEBUI_TEST_S3_ENDPOINT", "http://localhost:59000"),
        region="us-east-1",
        bucket=os.environ.get("FREE_WEBUI_TEST_S3_BUCKET", "freewebui"),
        access_key=os.environ.get("FREE_WEBUI_TEST_S3_KEY", "minioadmin"),
        secret_key=os.environ.get("FREE_WEBUI_TEST_S3_SECRET", "minioadmin"),
        path_style=True,
    )
    try:
        await store.ensure_bucket()
        await store.put("itest-key", PNG, "image/png")
        assert await store.get("itest-key") == PNG
        await store.delete("itest-key")
        assert await store.get("itest-key") is None
    finally:
        await store.aclose()
