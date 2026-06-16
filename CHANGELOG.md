# Changelog

This project uses [Keep a Changelog](https://keepachangelog.com/) conventions
and semantic versioning. Dates are not pinned to releases yet (pre-1.0).

## [Unreleased]

The 0.1 line grew from "core chat only" into a full self-host chat platform.
Highlights since the initial tiers (see git history + `docs/ROADMAP.md` for the
authoritative status):

### Added
- **Security hardening**: SSRF guard for user/operator URLs, authenticated
  `/api/models`, security-headers middleware, login rate-limiting, request
  body cap, finite upstream read timeout, bounded `calculate()`,
  `cookie_secure`/HSTS flags, request-id middleware.
- **Auth/RBAC**: server-side session revocation (`token_version`,
  logout-everywhere), **OIDC/SSO** (no extra dep), user **groups + per-model
  access control**, admin **audit log**, **feedback log**, **usage analytics**
  dashboard (`/admin/analytics` — totals, messages/day, top models, 👍/👎, active
  users; aggregated from existing tables, no new dep), and **broadcast banners**
  (admin-posted info/warning/error/success announcements shown to every user,
  client-side dismissible; `/admin/banners`).
- **Chat UX**: sidebar search + date grouping + rename + **pin/archive**,
  **non-destructive regenerate with variant navigation**, copy + 👍/👎 per
  message, **LLM auto-titling**, extra generation params
  (`max_tokens`/penalties/seed).
- **Knowledge bases**: reusable document **collections** attachable to any chat.
- **Faster RAG**: numpy-vectorized retrieval (`_rank_chunks`) replaces the
  per-chunk Python cosine loop with a single matrix-vector product — ~18× faster
  on 50k chunks with identical top-k (first runtime dependency added: `numpy`).
- **Custom assistants**: presets graduate into full assistants — model + persona
  (system prompt) + behavior (tools/web/params) + **bundled knowledge
  collections**; applying one configures the chat and attaches its knowledge;
  editable in place (`PUT /api/presets/{id}`). Cross-user collection access is
  filtered out on both save and attach.
- **Notes**: a per-user markdown notebook workspace (`/notes`) with live preview.
- **OpenAPI tool servers**: register a URL to an OpenAPI (3.x/JSON) spec; its
  operations become callable tools alongside built-ins + MCP — the tool dispatch
  is now a generic 3-source router, and every spec fetch + operation call is
  SSRF-guarded. Managed at `/account/openapi`.
- **Server-side voice**: STT/TTS proxies (`/api/audio/transcriptions`,
  `/api/audio/speech`) to any OpenAI-compatible audio backend; the mic records a
  clip and transcribes server-side, speak plays synthesized audio — both fall
  back to the browser Web Speech API when unconfigured.
- **Organization**: per-conversation **tags** (sidebar tag-filter chips +
  drawer editor) and **folders** (single-home, sidebar filter + drawer move),
  on top of search/pin/archive.
- **Temporary chat**: a stateless `/api/chat/temporary` endpoint + `/temporary`
  page — a throwaway conversation that is never written to the database.
- **Compare models**: a `/compare` page sends one prompt to up to 4 models in
  parallel, side by side (reuses the temporary-chat endpoint; nothing saved).
- **Composer UX**: **searchable model picker** (scales to many merged models)
  and in-composer commands — `/` insert a saved prompt, `@` switch model, `#`
  attach a knowledge collection — with keyboard navigation.
- **Per-message actions**: regenerate **any** assistant turn (not just the
  trailing one; branches the thread, prior reply kept as a variant), delete
  a message (truncates the thread from there), and **continue** a trailing reply
  that stopped early (appends to the same message); ♻/🗑/↪ buttons per message.
- **Object/media store**: base64 image payloads (generated + vision uploads)
  are externalized to a `files` blob table and served via `/api/files/{id}`
  instead of bloating every message row; re-inlined for upstream vision replay
  and public-share rendering.
- **Connectivity**: **multiple upstream connections** with per-model routing;
  hardened OpenAI `/v1` surface (+`/v1/embeddings`); **Anthropic `/v1/messages`
  proxy**.
- **Sharing**: public read-only conversation **share links**.
- **Real-time channels**: shared multi-user chat rooms with live **WebSocket**
  delivery (message broadcast, presence count, typing indicators) on top of REST
  CRUD + paginated history; an in-process broadcast hub, cookie-authenticated
  sockets, with REST→socket fallback and auto-reconnect.
- **Resilience**: a central `apiFetch` wrapper redirects to `/login?next=…`
  (with a toast) when a session expires mid-use instead of silently breaking the
  UI; reusable toast store + container; **clone conversation**.

### Engineering
- **Postgres backend** (opt-in via `FREE_WEBUI_DATABASE_URL=postgresql://…`):
  a backend-agnostic `Database` boundary with SQLite (aiosqlite, default) and
  Postgres (asyncpg) implementations. The **full test suite passes on both**,
  guarded by a dedicated Postgres CI job. SQLite stays the zero-config default;
  see `docs/SCALING.md`.
- Programmable upstream test fixture; **310+ backend tests** (run on SQLite +
  Postgres); ruff lint; GitHub Actions CI (lint + pytest matrix + frontend check).
- Security-sensitive features shipped with adversarial multi-agent review.
- Governance: `SECURITY.md`, `CONTRIBUTING.md` (clean-room attestation), PR
  template; planning docs under `docs/`.

### Fixed
- Migration ordering (indexes created after column migrations); the always-200
  test stub that hid every core error path; account-takeover via unverified
  OIDC email; fail-open model-access grants; per-request `/models` fan-out.
- Object-store hardening (adversarial review): cross-conversation blob exfil via
  forged `/api/files` refs; request-body cap bypass via chunked transfer-encoding
  (now meters streamed bytes) + base64 decode-amplification guard; unbounded
  inline amplification on replay/share (per-payload byte budget); orphaned blob
  reclamation (GC on message truncation + FK CASCADE on conversation delete);
  self-contained clones (copy their own blobs).
- Variant-tree corruption: regenerating after navigating to an older variant no
  longer forks the parent chain into two simultaneously-active replies; variant
  switching is restricted to the latest turn.
