"""Code interpreter: the subprocess backend (run for real), docker argv
hardening, backend resolution, status, gating, and run_python through the
tool loop. Docker is never required — its command construction is unit-tested
as a pure function and the live path uses the subprocess backend."""
import json

import httpx


async def _signup(client):
    await client.post(
        "/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"}
    )


# ---- subprocess backend (real execution) ----

async def test_subprocess_runs_and_captures_stdout(monkeypatch):
    from app import code_exec
    from app.config import settings

    monkeypatch.setattr(settings, "code_interpreter", "subprocess")
    result = await code_exec.execute("print(6 * 7)")
    assert result.backend == "subprocess"
    assert result.exit_code == 0
    assert not result.timed_out
    assert "42" in result.stdout
    assert result.images == []


async def test_subprocess_captures_stderr_and_exit_code(monkeypatch):
    from app import code_exec
    from app.config import settings

    monkeypatch.setattr(settings, "code_interpreter", "subprocess")
    result = await code_exec.execute("raise ValueError('boom')")
    assert result.exit_code not in (0, None)
    assert "ValueError" in result.stderr and "boom" in result.stderr


async def test_subprocess_strips_secrets_from_env(monkeypatch):
    """The child must not inherit FREE_WEBUI_* (api keys, signing key)."""
    from app import code_exec
    from app.config import settings

    monkeypatch.setattr(settings, "code_interpreter", "subprocess")
    monkeypatch.setenv("FREE_WEBUI_SECRET_KEY", "super-secret")
    result = await code_exec.execute(
        "import os; print(os.environ.get('FREE_WEBUI_SECRET_KEY', 'MISSING'))"
    )
    assert "MISSING" in result.stdout
    assert "super-secret" not in result.stdout


async def test_subprocess_timeout(monkeypatch):
    from app import code_exec
    from app.config import settings

    monkeypatch.setattr(settings, "code_interpreter", "subprocess")
    monkeypatch.setattr(settings, "code_timeout_seconds", 1.0)
    result = await code_exec.execute("while True:\n    pass")
    assert result.timed_out is True


async def test_subprocess_output_truncated(monkeypatch):
    from app import code_exec
    from app.config import settings

    monkeypatch.setattr(settings, "code_interpreter", "subprocess")
    monkeypatch.setattr(settings, "code_max_output_chars", 100)
    result = await code_exec.execute("print('x' * 5000)")
    assert "…(truncated)" in result.stdout
    assert len(result.stdout) <= 100 + len("\n…(truncated)")


async def test_subprocess_captures_produced_image(monkeypatch):
    from app import code_exec
    from app.config import settings

    monkeypatch.setattr(settings, "code_interpreter", "subprocess")
    code = (
        "open('plot.png', 'wb').write(b'\\x89PNG\\r\\n\\x1a\\n' + b'pixels' * 20)\n"
        "print('saved')"
    )
    result = await code_exec.execute(code)
    assert "saved" in result.stdout
    assert len(result.images) == 1
    assert result.images[0].startswith("data:image/png;base64,")


async def test_empty_code_is_a_noop(monkeypatch):
    from app import code_exec
    from app.config import settings

    monkeypatch.setattr(settings, "code_interpreter", "subprocess")
    result = await code_exec.execute("   \n  ")
    assert result.exit_code == 0 and result.stdout == "" and result.images == []


async def test_execute_disabled_raises(monkeypatch):
    from app import code_exec
    from app.config import settings

    monkeypatch.setattr(settings, "code_interpreter", "")
    try:
        await code_exec.execute("print(1)")
    except code_exec.CodeExecError as e:
        assert "not configured" in str(e)
    else:
        raise AssertionError("expected CodeExecError when disabled")


# ---- backend resolution ----

def test_resolve_backend(monkeypatch):
    from app import code_exec
    from app.config import settings

    monkeypatch.setattr(settings, "code_interpreter", "")
    assert code_exec._resolve_backend() == ""
    monkeypatch.setattr(settings, "code_interpreter", "subprocess")
    assert code_exec._resolve_backend() == "subprocess"
    monkeypatch.setattr(settings, "code_interpreter", "docker")
    assert code_exec._resolve_backend() == "docker"
    monkeypatch.setattr(settings, "code_interpreter", "auto")
    monkeypatch.setattr(code_exec.shutil, "which", lambda _n: "/usr/bin/docker")
    assert code_exec._resolve_backend() == "docker"
    monkeypatch.setattr(code_exec.shutil, "which", lambda _n: None)
    assert code_exec._resolve_backend() == "subprocess"


# ---- docker argv hardening (pure) ----

def test_docker_argv_is_hardened(monkeypatch, tmp_path):
    from app import code_exec
    from app.config import settings

    monkeypatch.setattr(settings, "code_docker_image", "python:3-alpine")
    monkeypatch.setattr(settings, "code_max_memory_mb", 256)
    monkeypatch.setattr(settings, "code_cpus", "0.5")
    monkeypatch.setattr(settings, "code_pids_limit", 64)

    argv = code_exec._docker_argv(tmp_path, "fw-codeexec-test")
    joined = " ".join(argv)
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges" in joined
    assert argv[argv.index("--user") + 1] == "65534:65534"
    assert argv[argv.index("--memory") + 1] == "256m"
    assert argv[argv.index("--memory-swap") + 1] == "256m"  # no swap
    assert argv[argv.index("--cpus") + 1] == "0.5"
    assert argv[argv.index("--pids-limit") + 1] == "64"
    assert f"{tmp_path}:/work:rw,z" in argv  # ,z = SELinux relabel
    assert argv[-4:] == ["python:3-alpine", "python", "-I", "-"]
    # the host filesystem must not be mounted in beyond the scratch workdir
    assert not any(spec.startswith("/:") or ":/host" in spec for spec in argv)


# ---- status endpoint + gating ----

async def test_code_status(client, monkeypatch):
    from app.config import settings

    await _signup(client)
    monkeypatch.setattr(settings, "code_interpreter", "")
    assert (await client.get("/api/code/status")).json() == {
        "available": False,
        "backend": None,
    }
    monkeypatch.setattr(settings, "code_interpreter", "subprocess")
    assert (await client.get("/api/code/status")).json() == {
        "available": True,
        "backend": "subprocess",
    }


def test_run_python_gated_on_config(monkeypatch):
    from app import tools

    monkeypatch.setattr(tools.settings, "code_interpreter", "")
    names = [s["function"]["name"] for s in tools.builtin_tool_specs()]
    assert "run_python" not in names

    monkeypatch.setattr(tools.settings, "code_interpreter", "subprocess")
    names = [s["function"]["name"] for s in tools.builtin_tool_specs()]
    assert "run_python" in names


def test_format_exec(monkeypatch):
    from app import code_exec, tools
    from app.config import settings

    assert "no output" in tools._format_exec(code_exec.ExecResult(exit_code=0))
    r = tools._format_exec(code_exec.ExecResult(stdout="hi", exit_code=0))
    assert r == "stdout:\nhi"
    monkeypatch.setattr(settings, "code_timeout_seconds", 15.0)
    assert "time limit" in tools._format_exec(code_exec.ExecResult(timed_out=True))
    assert "exited with code 1" in tools._format_exec(code_exec.ExecResult(exit_code=1))
    assert "1 image produced" in tools._format_exec(
        code_exec.ExecResult(exit_code=0, images=["data:image/png;base64,QUJD"])
    )


# ---- run_python through the streaming tool loop ----

async def test_run_python_tool_loop(client, monkeypatch):
    from app.config import settings
    from app.main import app
    from tests.test_images import _parse_sse, _read_stream, _stream_chunks

    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    await client.patch(f"/api/conversations/{cid}", json={"tools_enabled": True})
    monkeypatch.setattr(settings, "code_interpreter", "subprocess")

    count = {"n": 0}
    seen: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/chat/completions"):
            from tests.conftest import _fake_handler
            return await _fake_handler(request)
        count["n"] += 1
        seen.append(json.loads(request.content))
        if count["n"] == 1:
            return _stream_chunks([
                {"choices": [{"delta": {"tool_calls": [{
                    "index": 0, "id": "c1", "type": "function",
                    "function": {"name": "run_python", "arguments": json.dumps({"code": "print(6*7)"})},
                }]}}]},
                {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            ])
        return _stream_chunks([
            {"choices": [{"delta": {"content": "the answer is 42"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ])

    app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://upstream/v1"
    )

    text = await _read_stream(client, cid, "compute 6*7 in python")
    tool_events, _images, deltas, _order = _parse_sse(text)
    assert "run_python" in [t["function"]["name"] for t in seen[0]["tools"]]
    assert tool_events and tool_events[0]["name"] == "run_python"
    assert "stdout:" in tool_events[0]["result"] and "42" in tool_events[0]["result"]
    assert "".join(deltas) == "the answer is 42"


async def test_run_python_image_artifact_persisted(client, monkeypatch):
    from app.config import settings
    from app.main import app
    from tests.test_images import _parse_sse, _read_stream, _stream_chunks

    await _signup(client)
    cid = (await client.post("/api/conversations", json={})).json()["id"]
    await client.patch(f"/api/conversations/{cid}", json={"tools_enabled": True})
    monkeypatch.setattr(settings, "code_interpreter", "subprocess")

    code = "open('chart.png','wb').write(b'\\x89PNG\\r\\n\\x1a\\n'+b'd'*40)\nprint('done')"
    count = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/chat/completions"):
            from tests.conftest import _fake_handler
            return await _fake_handler(request)
        count["n"] += 1
        if count["n"] == 1:
            return _stream_chunks([
                {"choices": [{"delta": {"tool_calls": [{
                    "index": 0, "id": "c1", "type": "function",
                    "function": {"name": "run_python", "arguments": json.dumps({"code": code})},
                }]}}]},
                {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            ])
        return _stream_chunks([
            {"choices": [{"delta": {"content": "here's the chart"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ])

    app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://upstream/v1"
    )

    text = await _read_stream(client, cid, "make a chart")
    _tool, image_events, _deltas, _order = _parse_sse(text)
    assert image_events and image_events[0]["url"].startswith("data:image/png;base64,")

    conv = (await client.get(f"/api/conversations/{cid}")).json()
    content = json.loads(conv["messages"][-1]["content"])
    assert any(p.get("type") == "image_url" for p in content)


# ---- artifact-collection hardening ----

_PNG = b"\x89PNG\r\n\x1a\n"


def test_collect_images_skips_symlinks(tmp_path):
    """A symlink (even with an image suffix) must never be followed — that is
    the host-file exfiltration channel that defeats even the docker sandbox."""
    import os

    from app import code_exec

    secret = tmp_path / "secret.txt"
    secret.write_bytes(_PNG + b"TOPSECRET")  # looks like a png, lives outside wd
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "ok.png").write_bytes(_PNG + b"realpixels")  # control: should collect
    os.symlink(secret, wd / "evil.png")  # exfil attempt
    os.symlink("/etc/hosts", wd / "abs.png")  # absolute exfil attempt

    imgs = code_exec._collect_images(wd)
    assert len(imgs) == 1  # only the genuine in-workdir image
    import base64
    assert base64.b64encode(b"TOPSECRET").decode() not in imgs[0]


async def test_execute_skips_symlink_artifact(monkeypatch):
    from app import code_exec
    from app.config import settings

    monkeypatch.setattr(settings, "code_interpreter", "subprocess")
    result = await code_exec.execute(
        "import os; os.symlink('/etc/hosts', 'x.png'); print('done')"
    )
    assert "done" in result.stdout
    assert result.images == []


def test_collect_images_skips_hardlinks(tmp_path):
    """Hardlinks aren't symlinks and resolve inside the workdir, so the symlink
    guard alone wouldn't catch them — nlink>1 must be refused."""
    import base64
    import os

    import pytest

    from app import code_exec

    secret = tmp_path / "secret.bin"
    secret.write_bytes(_PNG + b"HARDSECRET")
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "ok.png").write_bytes(_PNG + b"pixels")  # control, nlink 1
    try:
        os.link(secret, wd / "hard.png")  # hardlink (nlink becomes 2)
    except OSError:
        pytest.skip("hardlinks not supported on this filesystem")

    imgs = code_exec._collect_images(wd)
    assert len(imgs) == 1  # only the genuine single-linked image
    assert base64.b64encode(b"HARDSECRET").decode() not in imgs[0]


def test_collect_images_skips_fifo(tmp_path):
    """A fifo named *.png must be skipped, not block the collector (the read
    would otherwise hang waiting for a writer)."""
    import os

    import pytest

    from app import code_exec

    try:
        os.mkfifo(tmp_path / "pipe.png")
    except (OSError, AttributeError):
        pytest.skip("mkfifo unsupported here")
    (tmp_path / "ok.png").write_bytes(_PNG + b"px")
    imgs = code_exec._collect_images(tmp_path)  # must return promptly
    assert len(imgs) == 1


def test_collect_images_count_cap(tmp_path):
    from app import code_exec

    for i in range(12):
        (tmp_path / f"img{i:02d}.png").write_bytes(_PNG + b"x" * 40)
    assert len(code_exec._collect_images(tmp_path)) == code_exec._MAX_IMAGES


def test_collect_images_per_file_cap(tmp_path, monkeypatch):
    from app import code_exec
    from app.config import settings

    monkeypatch.setattr(settings, "image_max_bytes", 32)
    (tmp_path / "big.png").write_bytes(_PNG + b"x" * 200)
    assert code_exec._collect_images(tmp_path) == []


def test_collect_images_combined_cap(tmp_path, monkeypatch):
    from app import code_exec
    from app.config import settings

    monkeypatch.setattr(settings, "image_max_bytes", 1500)
    blob = _PNG + b"x" * 1000  # ~1008 bytes each
    for name in ("a.png", "b.png", "c.png"):
        (tmp_path / name).write_bytes(blob)
    assert len(code_exec._collect_images(tmp_path)) == 1  # combined budget


def test_collect_images_excludes_svg(tmp_path):
    from app import code_exec

    (tmp_path / "x.svg").write_bytes(b"<svg><script>alert(1)</script></svg>")
    assert code_exec._collect_images(tmp_path) == []


def test_collect_images_requires_real_image_bytes(tmp_path):
    """A .png whose contents are not actually an image is rejected."""
    from app import code_exec

    (tmp_path / "fake.png").write_bytes(b"not really a png")
    assert code_exec._collect_images(tmp_path) == []


# ---- robustness: cleanup, truncation, concurrency, partial output ----

async def test_subprocess_stderr_truncated(monkeypatch):
    from app import code_exec
    from app.config import settings

    monkeypatch.setattr(settings, "code_interpreter", "subprocess")
    monkeypatch.setattr(settings, "code_max_output_chars", 100)
    result = await code_exec.execute("import sys; sys.stderr.write('y' * 5000)")
    assert "…(truncated)" in result.stderr
    assert len(result.stderr) <= 100 + len("\n…(truncated)")


async def test_workdir_cleaned_up_on_exception(monkeypatch):
    import glob
    import tempfile

    from app import code_exec
    from app.config import settings

    monkeypatch.setattr(settings, "code_interpreter", "subprocess")

    async def boom(_code, _workdir):
        raise RuntimeError("backend exploded")

    monkeypatch.setattr(code_exec, "_run_subprocess", boom)
    pattern = tempfile.gettempdir() + "/fw-codeexec-*"
    before = set(glob.glob(pattern))
    try:
        await code_exec.execute("print(1)")
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the backend error to propagate")
    assert not (set(glob.glob(pattern)) - before)  # no leaked workdir


async def test_partial_output_preserved_on_timeout(monkeypatch):
    """Output flushed before the wall-clock kill must survive (the bounded
    reader keeps it rather than discarding on the kill)."""
    from app import code_exec
    from app.config import settings

    monkeypatch.setattr(settings, "code_interpreter", "subprocess")
    monkeypatch.setattr(settings, "code_timeout_seconds", 1.0)
    result = await code_exec.execute(
        "import sys, time\nsys.stdout.write('PARTIAL\\n'); sys.stdout.flush()\ntime.sleep(30)"
    )
    assert result.timed_out is True
    assert "PARTIAL" in result.stdout


async def test_concurrent_executions_isolated(monkeypatch):
    import asyncio

    from app import code_exec
    from app.config import settings

    monkeypatch.setattr(settings, "code_interpreter", "subprocess")
    monkeypatch.setattr(settings, "code_max_concurrency", 2)
    ra, rb = await asyncio.gather(
        code_exec.execute("open('a.png','wb').write(b'\\x89PNG\\r\\n\\x1a\\nAAAA')"),
        code_exec.execute("open('b.png','wb').write(b'\\x89PNG\\r\\n\\x1a\\nBBBB')"),
    )
    assert len(ra.images) == 1 and len(rb.images) == 1
    assert ra.images != rb.images  # each run saw only its own artifact


async def test_child_killed_on_cancellation(monkeypatch):
    """If the chat client disconnects mid-run (the execute task is cancelled),
    the child process must be killed, not orphaned."""
    import asyncio

    from app import code_exec
    from app.config import settings

    monkeypatch.setattr(settings, "code_interpreter", "subprocess")
    monkeypatch.setattr(settings, "code_timeout_seconds", 30.0)  # so cancel wins

    spawned: dict = {}
    real_create = asyncio.create_subprocess_exec

    async def spy(*a, **k):
        proc = await real_create(*a, **k)
        spawned["proc"] = proc
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)

    task = asyncio.ensure_future(code_exec.execute("import time; time.sleep(30)"))
    await asyncio.sleep(0.4)  # let the child spawn and start
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    proc = spawned["proc"]
    await asyncio.wait_for(proc.wait(), timeout=5)
    assert proc.returncode is not None  # killed, not left running
