# free-webui — integration validation & CI plan

The Compose harness (`integration/`) is good but proves exactly **one** slice:
Ollama chat + native embeddings + the internal `/api` chat/RAG/tool/plugin
paths. Everything else the README advertises is mock-unit-tested only and never
exercised against a live wire. The biggest gap was **process**: there was no CI
at all — the ~134 unit tests, `svelte-check`, and the one-command integration
gate (`integration/run.sh`) never ran automatically.

`.github/workflows/ci.yml` now runs lint + backend tests (matrix) + frontend
check on every push/PR. ✅

Priority legend: `P0` first.

---

## Integration targets

### P1 — OpenAI-compat upstream contract across upstreams
Only Ollama is ever exercised live; the README advertises 5 (Ollama, vLLM,
LM Studio, llama.cpp-server, OpenAI). Validate `GET /models`, streaming +
non-stream `POST /chat/completions`, and `POST /embeddings` for each. Highest
value: the **embeddings batch + `index` ordering** (`rag.py` sorts response by
`item['index']`; `documents.py` batches all chunks in one call) — a provider
that omits/reorders `index` silently mis-maps vectors to chunks (a correctness
bug, not a 5xx). Confirm `admin_models.py` (hard-wired to Ollama-native
`/api/*`) degrades gracefully on non-Ollama upstreams.
*How:* compose profiles `--profile llamacpp` (CPU, best free OpenAI-compat
cross-check) + a recorded-fixture `mock-openai` service; provider-parametrized
`integration/tests/test_upstream_contract.py`; CI matrix over `{ollama,
llamacpp, mock-openai}`.

### P1 — externally-facing `/v1` passthrough (`openai_compat.py`)
The public SDK contract is never hit by the harness. Validate Bearer auth,
streaming + non-stream, and the 502 / `data:{error}` error mappings. **Surface
the timeout inconsistency:** the non-stream path pins `timeout=300` but the
shared client used `read=None` — a stalled upstream hung a streamed `/v1`
request forever. (The shared read timeout is now finite — see Phase 1.)
*How:* mint a key via `/api/api_keys`, drive the real `openai` SDK at
`base_url=…/v1`; add a `--profile faulty-upstream` mock for error/timeout paths.

### P1 — embeddings / RAG end-to-end (harden the existing live test)
Best-covered today, but the recall assertion is soft-xfail so a green run can
hide a retrieval-quality regression, and only Ollama's `nomic-embed-text` is
proven. Add a **deterministic retrieval-layer assertion** (embed a doc, assert
the correct chunk ranks first for a distinctive query — independent of the chat
model's IQ). Parametrize the embedding provider; add a negative test (bad
embedding model → clear 4xx/502, not a 200 with empty vectors).

### P1 — PWA / service worker / frontend build + SPA-behind-backend
The frontend is entirely absent from the harness. `adapter-auto` has **no chosen
target** — pick `adapter-static` (SPA) or `adapter-node` first so the build is
deterministic. Validate the manifest + no-cache service worker install/activate,
the SPA-served-behind-backend topology, and a browser smoke (login → send →
streamed reply).
*How:* a `frontend` CI job (`npm ci && npm run check && npm run build`) + a
Playwright `fullstack` profile against the Ollama upstream.

### P2 — image generation backends (OpenAI Images / A1111 / ComfyUI)
Well unit-tested (22) but no real wire contract. Validate each backend's HTTP
flow (incl. ComfyUI `/prompt → /history → /view`; note the default SD1.5 graph's
`model.safetensors` won't resolve without that checkpoint). SSRF on result-URL
fetch is now guarded (`netguard`, Phase 1).
*How:* a recorded-fixture `mock-image` service returning a tiny canned PNG;
optional nightly `--profile comfyui-live`.

### P2 — code-interpreter backends (docker vs subprocess)
Strong unit coverage (29) but neither backend runs live. Validate stdout +
matplotlib-PNG round-trip, and the **security properties against a real daemon**
(network unreachable, read-only rootfs, non-root, mem/pids/timeout bounded),
plus the symlink/hardlink exfil guard.
*How:* `--profile codeexec-docker` on a Linux runner with the host docker
socket; subprocess profile trivially on the existing backend container.

### P2 — MCP servers (JSON-RPC)
Only 2 mocked unit tests. Validate probe → `tools/list`, namespacing/dispatch,
a real `tools/call` round-trip fed back into the loop, and per-server resilience
(one unreachable server skipped without killing the chat).
*How:* a `--profile mcp` canned mock MCP server (deterministic, like the plugin
anchor test).

### P2 — SearXNG web search
Validate the **fail-soft contract** — `search()` returns `[]` on any failure so
a broken search never breaks a chat turn — plus `top_k` truncation.
*How:* a `mock-searxng` recorded-fixture service with toggleable failure modes.

---

## CI job graph (`.github/workflows/ci.yml`)

Landed (✅) / planned (⬜):

| Job | What | Status |
| --- | --- | --- |
| `lint` | `ruff check` (+ `ruff format --check`); `mypy` advisory | ✅ ruff · ⬜ mypy |
| `backend-test` | matrix py3.11/3.12, `pytest -q` (the ~134+ existing tests) | ✅ |
| `frontend` | `npm ci && npm run check` (+ `build` once an adapter is chosen) | ✅ check · ⬜ build |
| `integration-smoke` | `integration/run.sh` with `qwen2.5:0.5b`, cached model volume | ⬜ |
| `integration-contract-matrix` | `{ollama, llamacpp, mock-openai}` contract + `/v1` + embeddings-index | ⬜ |
| `e2e-browser` | Playwright login→chat→reply + SW/manifest (PRs to main / nightly) | ⬜ |
| `optional-subsystems` | mock-image/mcp/searxng + code-exec profiles (nightly) | ⬜ |
| `security-scan` | `pip-audit` + `npm audit` + `gitleaks` (flag the hard-coded `FREE_WEBUI_SECRET_KEY` in compose) + Dependabot/CodeQL | ⬜ |
| `image-build-publish` | slim non-root prod backend image + built frontend → GHCR on tags | ⬜ |

**Two risks to flag while extending the harness:** (1) the streaming upstream
read timeout was unbounded (fixed in Phase 1); (2) `integration/docker-compose.yml`
ships a hard-coded `FREE_WEBUI_SECRET_KEY` — add a `gitleaks` gate so it can
never be copied to prod.
