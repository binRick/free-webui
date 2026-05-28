"""Sandboxed Python execution. Exposed to the LLM through the built-in
`run_python` tool (see tools.py); the tool loop in conversations.py surfaces
any images the code produces (e.g. matplotlib plots) via the same data:-URL /
event:image path used by image generation.

Two backends, selected by FREE_WEBUI_CODE_INTERPRETER:
  - "docker": a genuine sandbox — the code runs in a throwaway container with
    no network, a read-only rootfs, a non-root user, dropped capabilities, and
    memory/cpu/pid limits. The host filesystem (DB, secret key) is unreachable.
  - "subprocess": a same-host child process with a wall-clock timeout, POSIX
    rlimits, a stripped environment, and an isolated temp working dir. This
    bounds runaway resource use and hides our secrets from the env, but it is
    NOT a security boundary: the code can still read the host filesystem.

Both pass the program over stdin to `python -I -` (isolated mode) and run in a
fresh per-call working directory that is scanned for produced images.
"""
from __future__ import annotations

import asyncio
import base64
import math
import os
import shutil
import signal
import stat
import sys
import tempfile
import uuid
import weakref
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .auth import current_user
from .config import settings

router = APIRouter(prefix="/api/code", tags=["code"])

# Raster image types only — no SVG, which can carry markup we'd rather not
# round-trip from attacker-influenced output into the page.
_IMAGE_MIMES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
_MAX_IMAGES = 8
_RLIMIT_FSIZE_BYTES = 64 * 1024 * 1024  # cap any single file the code writes
_READ_CHUNK = 65536

# Magic-byte signatures — we surface an artifact only if its *contents* are a
# known raster image, never on extension alone (defence against smuggling).
_IMAGE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

# Cap on concurrent executions across all chats. Keyed by event loop so a
# single semaphore is reused for the life of the app's loop without binding to
# a stale loop (which matters under per-test loops).
_semaphores: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _get_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _semaphores.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(max(1, settings.code_max_concurrency))
        _semaphores[loop] = sem
    return sem


class CodeExecError(Exception):
    """Raised when execution can't be attempted at all (e.g. disabled)."""


@dataclass
class ExecResult:
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    timed_out: bool = False
    images: list[str] = field(default_factory=list)
    backend: str = ""


class CodeStatus(BaseModel):
    available: bool
    backend: str | None = None


def _resolve_backend() -> str:
    """Map the configured value to a concrete backend. 'auto' prefers docker
    when the binary is present (we never silently downgrade at runtime — a
    failing docker surfaces an error rather than dropping to the weaker
    subprocess sandbox)."""
    backend = settings.code_interpreter
    if backend == "auto":
        return "docker" if shutil.which("docker") else "subprocess"
    if backend in ("docker", "subprocess"):
        return backend
    return ""


@router.get("/status", response_model=CodeStatus, dependencies=[Depends(current_user)])
async def status_endpoint() -> CodeStatus:
    backend = _resolve_backend()
    return CodeStatus(available=bool(backend), backend=backend or None)


# ---- helpers ----

def _decode(raw: bytes, truncated: bool) -> str:
    s = raw.decode("utf-8", "replace")
    return s + "\n…(truncated)" if truncated else s


def _sniff_mime(raw: bytes) -> str | None:
    for sig, mime in _IMAGE_MAGIC:
        if raw.startswith(sig):
            return mime
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


def _collect_images(workdir: Path) -> list[str]:
    """Encode raster images the run produced as data: URLs, oldest first.

    Hardened against exfiltration: a sandboxed program can drop an
    image-suffixed *symlink* (or a path under a symlinked dir) pointing at a
    host secret — and since this collector runs in the SERVER process over the
    (bind-mounted, world-writable) workdir, following it would leak files even
    the docker container can't reach. So we refuse symlinks, require the
    resolved path to stay inside the workdir, and verify the bytes are actually
    a raster image. Count, per-file, and combined size are all capped.
    """
    per_file_cap = settings.image_max_bytes or (10 * 1024 * 1024)
    combined_cap = per_file_cap  # total across all artifacts from one run
    root = workdir.resolve()
    out: list[str] = []
    total = 0
    try:
        candidates = sorted(workdir.rglob("*"))
    except OSError:
        return out
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    for p in candidates:
        if len(out) >= _MAX_IMAGES:
            break
        if p.suffix.lower() not in _IMAGE_MIMES:
            continue
        try:
            p.resolve(strict=True).relative_to(root)  # reject .. / parent escapes
        except (OSError, ValueError, RuntimeError):
            continue
        # Open with O_NOFOLLOW (a symlinked name fails atomically — closes the
        # is_symlink TOCTOU) and O_NONBLOCK (a fifo named *.png can't hang us),
        # then validate the *opened inode*: regular file, single-linked (no
        # hardlink to a host secret), within the per-file cap. fstat+read on the
        # fd are atomic with respect to path swaps.
        try:
            fd = os.open(p, flags)
        except OSError:
            continue
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                continue
            if not (0 < info.st_size <= per_file_cap):
                continue
            raw = os.read(fd, per_file_cap)
        except OSError:
            continue
        finally:
            os.close(fd)
        mime = _sniff_mime(raw)  # trust contents, not the extension
        if not raw or mime is None or total + len(raw) > combined_cap:
            continue
        total += len(raw)
        out.append(f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}")
    return out


# ---- subprocess backend ----

def _rlimits() -> None:
    """preexec_fn (child side): new session + resource limits."""
    import resource

    os.setsid()  # own process group so a timeout can kill the whole tree
    secs = math.ceil(settings.code_timeout_seconds) + 1
    for res, soft_hard in (
        (resource.RLIMIT_CPU, (secs, secs)),
        (resource.RLIMIT_FSIZE, (_RLIMIT_FSIZE_BYTES, _RLIMIT_FSIZE_BYTES)),
        (resource.RLIMIT_CORE, (0, 0)),
    ):
        try:
            resource.setrlimit(res, soft_hard)
        except (ValueError, OSError):
            pass
    # RLIMIT_AS (memory) is unreliable on macOS and can break interpreter
    # startup; RLIMIT_NPROC (fork-bomb cap) is enforced per real-uid, so a low
    # value on a busy multi-process box can wrongly block legit code. Both are
    # therefore Linux-only and best-effort — docker (--memory/--pids-limit) is
    # the real bound, and a timeout still kills the whole process group.
    if sys.platform.startswith("linux"):
        mem = settings.code_max_memory_mb * 1024 * 1024
        nproc = settings.code_pids_limit
        for res, soft_hard in (
            (resource.RLIMIT_AS, (mem, mem)),
            (resource.RLIMIT_NPROC, (nproc, nproc)),
        ):
            try:
                resource.setrlimit(res, soft_hard)
            except (ValueError, OSError, AttributeError):
                pass


def _kill_process_group(proc) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass


async def _run_subprocess(code: str, workdir: Path) -> tuple[str, str, int | None, bool]:
    # Minimal env: never inherit FREE_WEBUI_* secrets (api keys, signing key).
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(workdir),
        "TMPDIR": str(workdir),
        "MPLCONFIGDIR": str(workdir),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(workdir),
        env=env,
        preexec_fn=_rlimits,
    )

    async def _kill_async(_p) -> None:
        _kill_process_group(proc)

    return await _run(proc, code, _kill_process_group, _kill_async)


# ---- docker backend ----

def _docker_argv(workdir: Path, name: str, image: str | None = None) -> list[str]:
    image = image or settings.code_docker_image
    mem = f"{settings.code_max_memory_mb}m"
    return [
        "docker", "run", "--rm", "-i",
        "--name", name,
        "--network", "none",
        "--memory", mem, "--memory-swap", mem,  # equal -> no swap
        "--cpus", str(settings.code_cpus),
        "--pids-limit", str(settings.code_pids_limit),
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", "65534:65534",  # nobody
        "--tmpfs", "/tmp:rw,size=64m,mode=1777",
        # ,z relabels the bind mount for SELinux hosts (no-op elsewhere)
        "-v", f"{workdir}:/work:rw,z",
        "-w", "/work",
        "-e", "HOME=/work",
        "-e", "TMPDIR=/tmp",
        "-e", "MPLCONFIGDIR=/tmp",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        image,
        "python", "-I", "-",
    ]


async def _docker_cleanup(name: str) -> None:
    for argv in (["docker", "kill", name], ["docker", "rm", "-f", name]):
        try:
            p = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(p.wait(), timeout=10)
        except (asyncio.TimeoutError, OSError):
            pass


def _kill_proc(proc) -> None:
    try:
        proc.kill()  # terminates the `docker run` client
    except (ProcessLookupError, OSError):
        pass


async def _run_docker(code: str, workdir: Path) -> tuple[str, str, int | None, bool]:
    name = f"fw-codeexec-{uuid.uuid4().hex}"
    proc = await asyncio.create_subprocess_exec(
        *_docker_argv(workdir, name),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def _kill_async(_p) -> None:
        await _docker_cleanup(name)

    try:
        # allow a startup grace on top of the code's wall-clock budget
        return await _run(proc, code, _kill_proc, _kill_async, grace=10.0)
    finally:
        # Ensure the container is gone even if we were cancelled mid-run. shield
        # alone isn't enough — the await on it still raises on cancellation,
        # detaching the cleanup; so on CancelledError we await it to completion
        # before re-propagating.
        cleanup = asyncio.ensure_future(_docker_cleanup(name))
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup
            raise


# ---- shared run plumbing ----

async def _feed_stdin(proc, code: str) -> None:
    if proc.stdin is None:
        return
    try:
        proc.stdin.write(code.encode())
        await proc.stdin.drain()
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass


async def _read_capped(stream, cap: int) -> tuple[bytes, bool]:
    """Read up to `cap` bytes, then keep draining (discarding) so the process
    is never blocked on a full pipe. Returns (bytes, truncated)."""
    if stream is None:
        return b"", False
    buf = bytearray()
    truncated = False
    while True:
        chunk = await stream.read(_READ_CHUNK)
        if not chunk:
            break
        room = cap - len(buf)
        if room > 0:
            buf.extend(chunk[:room])
        if len(chunk) > max(room, 0):
            truncated = True
    return bytes(buf), truncated


async def _run(proc, code: str, kill_sync, kill_async, grace: float = 0.0):
    """Drive proc to completion or timeout. stdout/stderr are read concurrently
    and byte-capped (bounding parent memory) while preserving whatever was
    produced before a kill; the process group / container is terminated on
    timeout AND on cancellation (e.g. the chat client disconnecting)."""
    cap = max(1, settings.code_max_output_chars)
    out_task = asyncio.ensure_future(_read_capped(proc.stdout, cap))
    err_task = asyncio.ensure_future(_read_capped(proc.stderr, cap))
    feed_task = asyncio.ensure_future(_feed_stdin(proc, code))
    timed_out = False
    try:
        try:
            await asyncio.wait_for(
                proc.wait(), timeout=settings.code_timeout_seconds + grace
            )
        except asyncio.TimeoutError:
            timed_out = True
            await kill_async(proc)
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
        # process has exited (or been killed): streams reach EOF, readers finish
        try:
            out, out_trunc = await asyncio.wait_for(out_task, timeout=5)
        except asyncio.TimeoutError:
            out, out_trunc = b"", False
        try:
            err, err_trunc = await asyncio.wait_for(err_task, timeout=5)
        except asyncio.TimeoutError:
            err, err_trunc = b"", False
    finally:
        if proc.returncode is None:  # still alive => cancelled / unexpected exit
            kill_sync(proc)
        for t in (out_task, err_task, feed_task):
            t.cancel()
    return _decode(out, out_trunc), _decode(err, err_trunc), proc.returncode, timed_out


async def execute(code: str) -> ExecResult:
    """Run `code` in the configured sandbox; return captured output + any
    produced images. Raises CodeExecError only when execution can't be
    attempted (disabled); backend/runtime failures come back in the result."""
    backend = _resolve_backend()
    if not backend:
        raise CodeExecError("code interpreter is not configured on this server")
    if not code.strip():
        return ExecResult(backend=backend, exit_code=0)

    async with _get_semaphore():
        workdir = Path(tempfile.mkdtemp(prefix="fw-codeexec-"))
        try:
            # Only docker needs the dir world-writable (its non-root user writes
            # artifacts there); the subprocess backend runs as us, so leave the
            # mkdtemp default 0o700 — no need to widen it.
            if backend == "docker":
                os.chmod(workdir, 0o777)
            runner = _run_docker if backend == "docker" else _run_subprocess
            stdout, stderr, exit_code, timed_out = await runner(code, workdir)
            images = _collect_images(workdir)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    return ExecResult(
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        timed_out=timed_out,
        images=images,
        backend=backend,
    )
