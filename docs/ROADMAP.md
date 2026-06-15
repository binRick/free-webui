# free-webui — development roadmap & Open WebUI parity

> Working plan for continuing development toward (selective) Open WebUI parity.
> Constraints are unchanged: **clean-room, MIT, no upstream code, small readable
> diffs, short dependency list.** We target the ~95% workflow people actually
> want, not strict feature parity.

Status legend — effort `S`/`M`/`L`/`XL`; priority `P0` (table-stakes / risk) →
`P3` (niche). Items already shipped are not relisted (see the README roadmap).

---

## Where we are

All three README tiers are essentially shipped: streaming chat with a real
multi-iteration tool loop, persistence, markdown/KaTeX/mermaid, auth, per-chat
RAG, SearXNG web search, multimodal input, voice (Web Speech), Ollama model
management, presets, tools + MCP, memories, prompt library, export, an
OpenAI-compatible `/v1` surface, PWA, image generation, a hardened Docker code
interpreter, and inlet/outlet plugins.

The gaps are **depth, hardening, and process** — not missing tiers.

---

## Phase 1 — Harden the core (do first)

Security baseline + cheap correctness/DoS fixes, before widening the surface.
Most are `S`/`M` and high risk-reduction. **Partially landed on
`harden/phase1-security-ci` — see CHANGELOG below.**

| Item | Effort | Status |
| --- | --- | --- |
| SSRF guard for user/operator URLs (`netguard.py`) wired into `mcp._rpc` + `images` result-fetch | M | ✅ landed |
| Authenticate `GET /api/models` (anon catalogue leak) | S | ✅ landed |
| `cookie_secure` config flag (was hardcoded `secure=False`) | S | ✅ landed |
| Bound `ast.Pow` in `tools._safe_eval` (`10**1e9` big-int DoS) | S | ✅ landed |
| Security-headers middleware + tighten CORS off `*` + `allow_credentials` | S | ✅ landed |
| Finite upstream read timeout (was `read=None` → stalls hang forever) | S | ✅ landed |
| Request body-size cap + global JSON exception envelope (no stack leak) | S | ✅ landed |
| Login rate-limiting (per IP+username) | S | ✅ landed |
| `PRAGMA busy_timeout` in `db.open_db` | S | ✅ landed |
| Client-disconnect abort in the streaming tool loop | M | ⬜ deferred |
| Transaction-wrap regenerate/edit delete+insert | M | ⬜ deferred |
| Context/token budgeting (stop sending full history + all memories every turn) | M | ⬜ deferred |

## Phase 2 — Table-stakes chat UX (P0/P1, high-visibility)

| Item | Effort | Status |
| --- | --- | --- |
| Sidebar conversation **search + date grouping + rename** (+ fix nested button-in-anchor) | M | ✅ landed (backend `?q=` title/content filter; client date buckets; inline rename) |
| **Copy-whole-message** button + per-message thumbs up/down | S | ✅ landed (copy on all messages; 👍/👎 wired to feedback) |
| **Message branching** schema + non-destructive regenerate + variant nav | L | ✅ landed (messages `active`/`parent_id`; regenerate archives the prior variant; `GET …/variants` + `POST …/activate`; chat shows ◀ n/m ▶ on the trailing assistant to switch replies) |
| Feedback/rating table + thumbs up/down | M | ✅ landed (`message_feedback` upsert + per-message rating in GET) |
| Per-message regenerate/delete (any assistant turn) | S | ✅ landed (`POST …/messages/{id}/regenerate` branches at any assistant turn — discards later turns, archives the prior reply as a variant; `DELETE …/messages/{id}` truncates the thread from there. Per-message ♻/🗑 buttons in the chat UI.) |
| Uniform API error handling + toast store + 401-redirect | M | ✅ mostly landed (central `apiFetch` wrapper routes every API call; a mid-session 401 shows a toast and redirects to `/login?next=…`; reusable `toasts` store + `<Toasts/>` container). Per-call-site error toasting can still be adopted incrementally. |
| **LLM-based titling** + follow-up suggestions | M | ✅ landed (`…/autotitle` + `…/followups`; titling fires after the first exchange; follow-up chips appear after each turn). |
| Citations / sources, public share links, pin/archive | — | ✅ all landed (see below / git history) |

## Phase 3 — Differentiators (P1 depth)

| Item | Effort | Notes |
| --- | --- | --- |
| Scalable RAG index + hybrid retrieval | M | Replace pure-Python full-scan cosine (`rag.py`) with `sqlite-vec` / numpy-vectorized + optional BM25. |
| **Knowledge bases / collections** reusable across chats | L | ✅ landed (`collections` + `collection_documents`/`collection_chunks`; `/api/collections` CRUD + uploads; attach to a conversation via `PUT …/collections`; RAG searches the conversation's docs + every attached collection; `/collections` management page + chat-drawer attach). |
| **Multiple upstream connections** | L | ✅ landed (`connections` table; env upstream = implicit conn 0; admin CRUD + `/test` probe at `/admin/connections`; merged `/api/models` + `/v1/models`; per-model routing with a cached, concurrent `/models` resolver; keyless connections don't inherit the env key). |
| User **groups** + per-model access control | L | ✅ landed (`groups`/`group_members`/`model_access`; public-unless-restricted; enforced on `/api/models`, `/v1/models`, chat send/regen/edit, `/v1/chat/completions`, autotitle, and conversation create/patch; admin UI at `/admin/access`). Granular per-feature RBAC still ⬜. |
| Server-side session store / revocation | M | ✅ landed (`users.token_version`; password reset + "log out everywhere" bump it; `current_user` rejects stale cookies). Role changes already apply live (role is read from the DB each request). |
| **OAuth / OIDC SSO** | L | ✅ landed (auth-code flow + userinfo, **no extra dep**; https-enforced; signed-state CSRF; verified-email account linking + auto-provision; first-user/admin-email role mapping; `/admin`-less login-page SSO button). |
| Decompose the 1194-LOC chat route + Vitest harness | L | Split into MessageList/MessageItem/Composer/SettingsDrawer; cache status endpoints. |
| Missing generation params | M | ✅ landed (per-chat `max_tokens`, `presence_penalty`, `frequency_penalty`, `seed`; persisted + forwarded; settings-drawer inputs). In-composer `/` prompts · `@` models · `#` knowledge commands ✅ + searchable model picker ✅ |
| **Anthropic `/v1/messages` proxy** | M | ✅ landed (`/anthropic/v1/messages`; translates Anthropic ↔ OpenAI incl. streaming; `x-api-key` auth; access-controlled + connection-routed — Claude Code / the Anthropic SDK can target free-webui). |

## Phase 4 — Larger bets (infra + enterprise/collab)

Real migration framework (versioned, not ADD-COLUMN-only) → ~~object/media
store for images (base64-in-SQLite today)~~ ✅ landed (`files` table + blob
store; base64 image payloads externalized to `/api/files/{id}` at persist time;
re-inlined for upstream vision replay and public-share rendering) → observability
+ audit log → ~~Anthropic `/v1/messages` proxy~~ ✅ + harden `/v1` → analytics +
feedback log → notes → server-side voice (STT/TTS).

## Phase 5 — Big architectural bets (XL, optional)

Postgres + horizontal scaling (the single-process single-SQLite-connection
design is the blocker) → Helm chart → real-time channels → full i18n + RTL.
Pursue only if the product targets teams/enterprise.

---

## Quick wins (S, high value)

- Authenticate `/api/models` ✅ · bound `ast.Pow` ✅ · `cookie_secure` flag ✅
  · `PRAGMA busy_timeout` ✅ · security headers ✅ · upstream read timeout ✅
  · login throttle ✅ (all landed in Phase 1).
- Copy-whole-message button (reuse the code-block copy pattern).
- In-app modal to replace `window.prompt/confirm/alert` (mobile + testability).
- Cache capability/status endpoints so `runStream`'s post-send `load()` stops
  refetching 6–7 endpoints every message.

## Big bets

- Postgres + horizontal scaling (root blocker for every scaling/enterprise feature).
- Message branching as a first-class data model.
- Full enterprise auth stack (OIDC + LDAP/SCIM + groups/RBAC + server-side sessions).
- Real-time channels (only if pivoting toward team collaboration).

---

## Program-level workstreams (orthogonal, interleave with the above)

The engineering roadmap above is incomplete without these — mostly P0/P1, cheap:

- **Clean-room / license governance (P0):** `CONTRIBUTING.md` with a per-PR
  "no upstream code/strings/assets" attestation, `SECURITY.md` disclosure path,
  a CI license-compat gate (vet every new dep: authlib, sqlite-vec, STT/TTS
  SDKs, mermaid), a `THIRD-PARTY-NOTICES` file. Protects the project's premise.
- **Prompt-injection threat model (P0):** RAG/web-search inject untrusted
  content into a model that can call `run_python`/`imagine`/MCP — an indirect
  injection → tool-abuse chain. Label injected content as untrusted; add a test
  that a poisoned doc can't escalate to a tool call.
- **Docs (P1):** `docs/` is images only. Admin/deploy guide, security-hardening
  checklist, plugin/tool authoring guide, `/v1` + `/api` reference, ADRs.
- **Data lifecycle (P1):** SQLite backup/restore, full-instance export/import,
  per-user GDPR export + erasure, retention policy.
- **Accessibility (P1):** `aria-live` for streaming tokens, accessible names on
  icon-only buttons, focus trapping, contrast, `prefers-reduced-motion`.
- **Release engineering (P1):** semver + CHANGELOG, signed artifacts/SBOM, an
  N-1-DB-opens-on-N migration test. (README still says "v0.0.1 — core chat".)
- **Quotas / cost caps (P1):** per-user/group rate limits + token/spend budgets
  on both chat and `/v1` for paid upstreams.

See `docs/TESTPLAN.md` and `docs/INTEGRATION.md` for the test backlog and the
integration-validation + CI plan.
