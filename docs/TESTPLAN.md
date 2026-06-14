# free-webui — test backlog

The backend suite (134 → growing) skews toward the newest Tier-3 features
(`code_exec`, `plugins`, `images` are well covered) while the load-bearing
749-LOC `conversations.py` core and RAG ranking are thin, and the frontend,
the `/v1` contract, and CI had zero coverage.

**The keystone enabler** was the upstream test fixture: the old
`conftest._fake_handler` *always returned 200*, making every hard path in
`conversations.py` unreachable. `conftest.py` now exposes a programmable
`FakeUpstream` (`upstream` fixture) + SSE builders (`sse`, `content_chunk`,
`tool_call_chunk`, `finish`, `error_response`) so tests can inject errors,
multi-iteration tool calls, and malformed frames. ✅

Status: ✅ landed on `harden/phase1-security-ci` · ⬜ backlog.

---

## P0 — core engine (`conversations.py` `_stream_and_persist`)

| Test | Type | Status |
| --- | --- | --- |
| Upstream `>=400` surfaces one `data:{error}` frame + `[DONE]`, persists no assistant row | integration | ✅ |
| Tool loop respects `max_tool_loops` (exactly 5 upstream calls, terminates) | integration | ✅ |
| Intermediate upstream `[DONE]` swallowed → client sees exactly one `[DONE]` | integration | ✅ |
| Empty completion (finish-only, no content) is not persisted | integration | ✅ |
| Malformed tool-call `arguments` fall back to `{}`, tool still runs | integration | ✅ |
| Assistant multimodal image downcast to `[generated an image]` on replay | integration | ⬜ |
| Client disconnect persists partial once + emits `[DONE]` (needs disconnect-abort) | integration | ⬜ |

## P1 — regenerate / edit truncation + ownership

| Test | Type | Status |
| --- | --- | --- |
| `regenerate` → 400 when no trailing assistant; deletes nothing | integration | ⬜ |
| `edit` rejects non-user (400) / missing message (404); deletes nothing | integration | ⬜ |
| `edit` truncates **all** trailing turns past the edited message | integration | ⬜ |
| `regenerate`/`edit` are owner-scoped (cross-user → 404) | security | ⬜ |
| web-search/RAG context injected on regenerate + edit (spy upstream) | integration | ⬜ |

## P1 — RAG retrieval correctness (`rag.py`)

Ranking math is unverified (toy conftest embeddings are near-identical).

- Ranking + top-k truncation: closest chunk ranks first, exactly `top_k` returned. ⬜
- `top[0] score <= 0` → returns `None` (no context injected). ⬜
- Embeddings reordered by `index` when the response arrives shuffled. ⬜
- `embed_texts` upstream 5xx/network error → 502 with documented detail. ⬜
- `chunk_text` overlap clamp + whitespace-window skip + boundary count. ⬜
- `cosine` length-mismatch + zero-vector → `0.0` (no raise). property ⬜

## P1 — `/v1` OpenAI-compatible contract (`openai_compat.py`)

- Non-stream completion verbatim; upstream 500 → 502; non-JSON → 502; network → 502. ⬜
- Bearer rejections: missing / wrong scheme / empty / unknown token → 401. ⬜
- Stream upstream error → single `data:{error}` frame, stream ends. ⬜
- `last_used_at` bumped on a successful call. ⬜
- API-key revoke is owner-scoped (B can't revoke A's key). security ⬜

## P1 — frontend (new Vitest harness — none exists yet)

- `consumeStream` routes content / `event:tool_call` / `event:image` / keep-alive / `[DONE]`. ⬜
- `consumeStream` reassembles a frame split across two reader chunks. ⬜
- `consumeStream` ignores malformed JSON, keeps going. ⬜
- `parseContent` matches backend `_decode_content` byte-for-byte (JSON array vs `[nope]` vs plain). ⬜
- `renderMarkdown` strips `<script>`, `on*=`, `javascript:` (DOMPurify; `ADD_ATTR` includes `style`, SVG on). security ⬜

## P1 — security (landed)

| Test | Type | Status |
| --- | --- | --- |
| SSRF guard blocks metadata/loopback/private/link-local; allows public + allowlisted + unresolvable | unit | ✅ `test_netguard.py` |
| MCP probe against `169.254.169.254` is refused (guard on) | security | ✅ |
| `GET /api/models` requires auth (401 → 200 after login) | integration | ✅ `test_security.py` |
| Security headers present on every response | integration | ✅ |
| `cookie_secure=True` issues a `Secure` cookie | integration | ✅ |
| Body-size cap → 413 | integration | ✅ |
| Login throttle → 429 after N attempts | security | ✅ |
| `calculate('10**100000000')` returns a bounded error, doesn't hang | security | ✅ `test_tools.py` |

## P1 — DB (`db.py`, was zero tests — landed)

- `open_db` sets `busy_timeout`. ✅
- FK cascade: deleting a user removes conversations + messages. ✅
- Additive migration adds missing columns without data loss. ✅
- `open_db` idempotent (second call preserves rows). ✅

## P2 — broader sweeps (backlog)

- Cross-user delete-by-id scoping across memories/presets/prompts/documents. ⬜
- MCP resilience: one bad server skipped; RPC error mappings; server-deleted-mid-turn fallback; namespacing collisions. ⬜
- Auth session forgery/expiry/deleted-user; secret-key persisted + reused. ⬜
- Docker sandbox security (gated): network-none, read-only rootfs, non-root; symlinked-dir artifact refused. ⬜
- Built-in tools dispatch (unknown/raising handler) + `web_search` fail-soft (`[]` on any error). ⬜

---

## Test infrastructure

| Item | Status |
| --- | --- |
| Programmable upstream fixture + SSE builders | ✅ |
| Backend `pytest` + matrix in CI | ✅ (`.github/workflows/ci.yml`) |
| `ruff` lint config + CI job | ✅ |
| `mypy` (advisory → ratchet to blocking) | ⬜ |
| `pytest-cov` coverage gate | ⬜ |
| Frontend Vitest + `@testing-library/svelte` | ⬜ |
| Playwright e2e against the compose harness | ⬜ |
| SSE/`_decode_content` shared wire-contract golden fixture (SPA ↔ `/v1`) | ⬜ |
