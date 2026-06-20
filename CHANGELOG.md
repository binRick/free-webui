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
- **Per-feature permission matrix**: a fine-grained capability gate for non-admin
  users — web search, image generation, code interpreter, file upload, external
  (MCP/OpenAPI) tools, knowledge bases, notes, temporary chat, and public share
  links. Every capability defaults to *allowed* (opt-in restriction); an admin
  flips a global default off and optionally grants it back to specific **groups**
  (effective = default OR any group grant). Admins always bypass. Enforced
  server-side at every surface (tool composition + the upload/knowledge/notes/
  temporary/share endpoints) and reflected in the chat UI via
  `GET /api/permissions/me`; managed at `/admin/permissions`.
- **Chat UX**: sidebar search + date grouping + rename + **pin/archive**,
  **non-destructive regenerate with variant navigation**, copy + 👍/👎 per
  message, **LLM auto-titling**, extra generation params
  (`max_tokens`/penalties/seed).
- **Knowledge bases**: reusable document **collections** attachable to any chat.
- **Faster RAG**: numpy-vectorized retrieval (`_rank_chunks`) replaces the
  per-chunk Python cosine loop with a single matrix-vector product — ~18× faster
  on 50k chunks with identical top-k (first runtime dependency added: `numpy`).
- **Hybrid RAG retrieval**: dense embedding cosine and sparse **BM25** keyword
  scores are fused with **Reciprocal Rank Fusion**, so exact-term matches
  (names, IDs, error codes, code symbols) that pure embeddings miss are
  retrieved alongside semantic neighbours. Scale-free fusion (no score
  normalisation); BM25 is pure-Python (no new dependency). Toggle with
  `FREE_WEBUI_RAG_HYBRID=false` for vector-only.
- **Custom assistants**: presets graduate into full assistants — model + persona
  (system prompt) + behavior (tools/web/params) + **bundled knowledge
  collections**; applying one configures the chat and attaches its knowledge;
  editable in place (`PUT /api/presets/{id}`). Cross-user collection access is
  filtered out on both save and attach.
- **Notes**: a per-user markdown notebook workspace (`/notes`) with live preview.
- **Account data lifecycle**: a self-service **data export** (`GET /api/account/export`
  bundles the caller's conversations, messages, prompts, notes, memories, presets,
  collections, folders, feedback, and API-key metadata — never any secret/hash —
  into one JSON download) and **account deletion** (`DELETE /api/account`,
  password-re-authenticated; cascades away all the user's data, reclaims their S3
  objects, clears the session; the last admin can't delete themselves). Wired into
  the `/account` page.
- **OpenAPI tool servers**: register a URL to an OpenAPI (3.x/JSON) spec; its
  operations become callable tools alongside built-ins + MCP — the tool dispatch
  is now a generic 3-source router, and every spec fetch + operation call is
  SSRF-guarded. Managed at `/account/openapi`.
- **Server-side voice**: STT/TTS proxies (`/api/audio/transcriptions`,
  `/api/audio/speech`) to any OpenAI-compatible audio backend; the mic records a
  clip and transcribes server-side, speak plays synthesized audio — both fall
  back to the browser Web Speech API when unconfigured.
- **Voice/video call mode** (`/call`): a hands-free conversational loop —
  listen (STT) → think (streamed model) → speak (TTS) → listen — with WebAudio
  RMS **voice-activity detection** for natural turn-taking (Web Speech endpointing
  as the fallback), a live transcript, mute, and an optional **camera** that
  attaches a frame to each turn for vision models. The temporary-chat endpoint
  now accepts multimodal user content (text + inline `data:` images, bounded;
  remote image URLs refused so it can't be coerced into an SSRF fetch agent).
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
- **S3/object storage** (opt-in via `FREE_WEBUI_S3_BUCKET`): file bytes can live
  in an S3-compatible bucket (AWS S3, **MinIO**, Ceph, Backblaze B2) instead of
  the DB — the `files` row stays the canonical index (access control, GC) with
  `storage='s3'`. AWS **SigV4** signing is pure-standard-library (no new
  dependency), validated against AWS's documented test vector and a real MinIO
  round-trip. Access control still precedes any object fetch; conversation
  delete and GC remove the S3 objects the FK cascade can't reach. SQLite/DB
  storage stays the zero-config default.
- **Connectivity**: **multiple upstream connections** with per-model routing;
  hardened OpenAI `/v1` surface (+`/v1/embeddings`); **Anthropic `/v1/messages`
  proxy**.
- **Sharing**: public read-only conversation **share links**.
- **Real-time channels**: shared multi-user chat rooms with live **WebSocket**
  delivery (message broadcast, presence count, typing indicators) on top of REST
  CRUD + paginated history; an in-process broadcast hub, cookie-authenticated
  sockets, with REST→socket fallback and auto-reconnect.
- **Global login throttle** (when `FREE_WEBUI_REDIS_URL` is set): the login
  rate-limiter uses a shared Redis `INCR`+`EXPIRE` counter, so the limit holds
  across replicas instead of being N× looser per worker; falls back to the
  in-process window when Redis is absent or unreachable (logins never lock out on
  a Redis hiccup). Closes scaling blocker B2.
- **Cross-replica channels** (opt-in via `FREE_WEBUI_REDIS_URL`): the channel hub
  fans frames out through a pluggable transport — in-process by default, or
  **Redis pub/sub** so N stateless replicas share channel traffic (each replica's
  subscriber delivers to its local sockets; the publisher delivers via its own
  subscription, so there's exactly one delivery path). `redis` is an optional,
  lazily-imported dependency. Closes the first horizontal-scaling blocker in
  `docs/SCALING.md`; validated against a real Redis (two-instance fan-out + a
  REST-post→Redis→socket round-trip). Presence count / per-user WS cap stay
  per-replica for now.
- **Evaluation suite**: a model **arena** (`/arena`) runs blind A/B battles —
  two anonymised models answer one prompt, you vote a winner/tie/both-bad, and
  identities are revealed after the vote — plus a **leaderboard**
  (`/evaluations`) that ranks models by arena **ELO** (replayed deterministically
  from the vote log) and by a Wilson-scored 👍/👎 feedback rate. Assistant
  messages now record the model that produced them (`messages.model`), which the
  leaderboard and usage analytics both use. Arena votes are access-gated to
  models the voter may actually use; the raw vote log is admin-only.
- **i18n foundation**: a dependency-free reactive `t()` + per-locale JSON
  catalogs (**en/es/fr/de**) with an in-app language switcher; sidebar + login
  wired, the rest of the UI adopting `t()` incrementally.
- **Resilience**: a central `apiFetch` wrapper redirects to `/login?next=…`
  (with a toast) when a session expires mid-use instead of silently breaking the
  UI; reusable toast store + container; **clone conversation**.

### Engineering
- **Streaming correctness trio** (Phase-1 hardening): (1) **client-disconnect
  abort** — the streaming generator stops reading the upstream and stops the tool
  loop when the client goes away (closed tab / stop button) instead of running to
  completion and billing tokens after nobody's listening; the partial reply is
  persisted to match the on-screen text. (2) **transactional mutations** — a new
  `db.transaction()` (commit-on-success / rollback-on-error) wraps the
  send/edit/regenerate/delete blocks so a mid-sequence failure can't leak pending
  writes onto the shared connection for the next request's commit to flush.
  (3) **context budgeting** — `max_context_messages`/`max_context_tokens` bound the
  history replayed each turn and `max_memory_items` bounds injected memories,
  instead of resending the whole transcript + every memory forever. Disconnect
  change shipped with adversarial review (caught + fixed a regenerate-abort
  data-loss regression).
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
- Redis channel broadcaster (adversarial review): the subscriber now **self-heals**
  on a dropped Redis connection (supervised reconnect with bounded backoff +
  logging, instead of dying silently and going dark); per-socket sends run
  **concurrently with a deadline** so one backpressured client can't head-of-line
  stall the shared subscriber and freeze delivery for every channel; `aclose()`
  no longer swallows shutdown cancellation; `use_redis()` is idempotent.
- S3 object store (adversarial review): cross-tenant blob exfil via **clone**
  (`clone_file_refs` now conversation-scoped like the replay path — a forged ref
  to another conversation's blob is no longer re-owned by the cloner); SigV4
  `SignatureDoesNotMatch` when the endpoint carried an explicit default port
  (signed Host now matches the wire Host); user-deletion S3-object leak; S3
  deletes deferred to **after** the DB commit so a rollback can't strand a live
  row over a deleted object; `ensure_bucket` `LocationConstraint` for non-us-east-1.
- Variant-tree corruption: regenerating after navigating to an older variant no
  longer forks the parent chain into two simultaneously-active replies; variant
  switching is restricted to the latest turn.
