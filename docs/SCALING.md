# Scaling: Postgres + horizontal replicas

**Status: Phase 1 (Postgres support) — landed.** The `Database` boundary
(`app/database.py`) now has a working **asyncpg backend**: set
`FREE_WEBUI_DATABASE_URL=postgresql://…` and the app runs on Postgres instead of
SQLite. The **entire test suite passes on both backends** (a `backend-test-postgres`
CI job runs it against a Postgres service via `FREE_WEBUI_TEST_DATABASE_URL`).
SQLite remains the zero-config default.

**Phase 2 (started): cross-replica real-time channels — landed.** The first
in-process-state blocker (B1) is closed: the `ChannelHub` now fans frames out
through a pluggable transport (`app/broadcaster.py`). Set `FREE_WEBUI_REDIS_URL`
and every channel `message`/`typing`/`presence` frame is published to Redis
pub/sub and delivered to local sockets on **every** replica's subscriber, so the
workspace chat no longer fragments per-replica. `redis` is an optional, lazily
imported dependency; unset → the in-process hub (single-replica) as before.
Validated against a real Redis (two-instance fan-out + a REST-post→Redis→socket
round-trip).

**The per-user WS connection cap + presence count (B10) are now global too.** When
`FREE_WEBUI_REDIS_URL` is set, the `ChannelHub` keeps the per-user connection count
(`fw:wsconn:{uid}`) and the per-channel presence count (`fw:presence:{channel}`) in
shared Redis `INCR`/`DECR` counters (each with a refreshed `EXPIRE` TTL so a
crashed replica that never decrements self-heals after a quiet window rather than
leaking forever — the cap is a coarse backstop, not exact). Any Redis error falls
back to the in-process counters, so a hiccup never errors a connection. Unset →
per-replica counters as before.

The Redis-backed **login rate-limiter** (B2) also landed: a global `INCR`+`EXPIRE`
fixed-window counter when `FREE_WEBUI_REDIS_URL` is set (in-process sliding window
otherwise, and as the fail-open fallback on any Redis error).

Remaining: the rest of Phase 2 (OIDC first-admin lock B3, etc. — §3/§5) and the
connection-pool/per-request-transaction
refinement (the Postgres
backend currently uses one lock-serialized connection per replica, the same
concurrency profile as the shared SQLite connection).

This document inventories every blocker between today's
single-process / single-SQLite design and a deployment that (a) runs against
**Postgres** and (b) runs **N stateless replicas** behind a load balancer, then
proposes a phased plan and a concrete first increment. It is informed by an
architecture survey of `backend/app` (file:line references below are from that
survey).

The headline: **the auth/session layer already scales** (stateless signed
cookies + DB-backed revocation), so the work is concentrated in two areas —
**SQL-dialect portability** and **externalizing a handful of in-process
singletons**. Neither is conceptually hard; the cost is breadth (raw SQL is used
across ~30 modules) and a few genuinely distributed-systems decisions
(real-time fan-out, migration coordination).

---

## 1. What already scales (the good news)

| Concern | Why it's already replica-safe |
| --- | --- |
| **Sessions** | Signed-cookie sessions via `itsdangerous` (`auth.py:36-52`); any replica validates any cookie. No server-side session store. |
| **Revocation** | `token_version` lives in the DB (`current_user` checks it every request, `auth.py:102-118`); logout-everywhere / password-reset propagate through the shared DB to all replicas — including, after a recent fix, live channel WebSockets. |
| **Blob/object store** | Images & attachments are in the `files` table and served from `files.py` — already shared across replicas once the DB is shared (no local-disk dependency). |
| **Middlewares** | Security-headers, body-limit, request-id, CORS, exception handler are all stateless (`main.py:51-158`). |
| **Health** | `GET /api/health` (`main.py:250`) works on any replica. |

**Hard prerequisite:** the signing secret must be **shared** across replicas
(see §3.2). Today it auto-generates to a per-replica local file.

---

## 2. Blocker A — Postgres SQL-dialect portability

The backend uses raw SQL through `aiosqlite` with `?` placeholders, a single
shared connection on `app.state.db`, and `executescript` for schema bootstrap.
Most SQL is portable; the SQLite-isms are concentrated:

| # | Issue | Where | Postgres equivalent | Effort |
| --- | --- | --- | --- | --- |
| A1 | **`cursor.lastrowid`** after INSERT (16 paths) | `auth.py:198`, `oidc.py:172`, `admin_users.py:67`, `presets.py:156`, `documents.py:104`, `prompts.py:67`, `notes.py:67`, `folders.py`, `channels.py`, `collections.py`, … | Append `RETURNING id` and read the row (also valid on modern SQLite, so it can be unconditional). | M |
| A2 | **`?` qmark placeholders** everywhere (35+ sites) + dynamic `IN (?,?,…)` builders (`presets.py:61`) | every SQL call site | Normalize paramstyle at a DB boundary (`?` → `$N`/`%s`). The single biggest mechanical change. | L |
| A3 | **`executescript`** (multi-statement DDL) | `db.py:379, 384` | Split on `;` and execute statements individually behind the abstraction; keep DDL idempotent (`CREATE … IF NOT EXISTS`). | S |
| A4 | **`INTEGER PRIMARY KEY AUTOINCREMENT`** (20 tables) | `db.py` (every `id` column) | `BIGINT GENERATED BY DEFAULT AS IDENTITY` (or `BIGSERIAL`). Falls out of per-backend DDL generation. | S |
| A5 | **Migrations via `PRAGMA table_info` + `ALTER TABLE ADD COLUMN`** | `db.py:336-368` (`_MIGRATIONS`, `_ensure_columns`) | `information_schema.columns` for the column check; ALTER ADD COLUMN is portable. (Or adopt Alembic — see §4.) | M |
| A6 | **`PRAGMA` (WAL / foreign_keys / busy_timeout)** | `db.py:374-378` | Skip entirely on Postgres; FK enforcement is automatic; use a **connection pool** instead of one shared connection. | S |
| A7 | **`BLOB` columns** + `struct.pack` / `np.frombuffer` round-trip | `db.py:80,118,246` (embeddings, `files.data`) | `BYTEA`; standardize on **asyncpg** (native `bytes` both ways → least churn). | M |
| A8 | **`COLLATE NOCASE`** — LIKE search, ORDER BY, **and security-relevant username equality** | `conversations.py:557,561` (search), `folders.py:41` (order), **`oidc.py:119,151` (username lookup/uniqueness)** | `ILIKE` for searches; `lower(col) = lower(?)` (or `citext`) for the username cases. ⚠️ The oidc.py uses enforce **case-insensitive username uniqueness** to block duplicate-identity account takeover — the migration MUST preserve case-insensitive matching there, not silently drop the collation. | M |
| A9 | **`strftime(…,'unixepoch')`** day bucketing | `admin_analytics.py:82` | `to_char(to_timestamp(created_at) AT TIME ZONE 'UTC','YYYY-MM-DD')` — or compute day buckets in Python. | T |
| A10 | **`GROUP_CONCAT`** (tags) | `conversations.py:569` | `string_agg(tag, ',')`. | T |
| A11 | **`||` string concat** in UPDATE (continue-gen) | `conversations.py:500` | Portable; harden to `COALESCE(content,'') || ?`. | T |
| A12 | **Integer-as-boolean** columns + `= 1/= 0` | `db.py` (web_search, tools_enabled, pinned, archived, active, enabled) | Keep columns `INTEGER` in Postgres too (it accepts `= 1`) — zero churn. Don't switch to `BOOLEAN`. | T |

**Single shared connection → pool.** `open_db` returns one `aiosqlite`
connection stored on `app.state.db`; every router shares it and WAL serializes
writes. Postgres wants a **pool** (`asyncpg.create_pool`) with per-request
acquisition and explicit transactions. The `~150` `app.state.db.execute(...)`
call sites assume "one connection object with `.execute/.commit`" — the DB
abstraction (§4) must preserve that ergonomic while backing it with a pool.

---

## 3. Blocker B — in-process state (horizontal scaling)

State that lives in one replica's memory either **breaks** (real-time fan-out)
or **silently diverges** (rate limits N× looser) across replicas.

### 3.1 Must externalize

| # | Singleton | Where | What breaks with 2+ replicas | Fix | Effort |
| --- | --- | --- | --- | --- | --- |
| B1 | **`ChannelHub`** real-time broadcast ✅ **done** | `channels.py` + `broadcaster.py` | A channel message/presence/typing only reaches clients on the **same replica** — the workspace chat silently fragments. | **Redis pub/sub** (landed): each frame is published to `freewebui:channel:{id}`; every replica's subscriber fans out to *its* local sockets. Set `FREE_WEBUI_REDIS_URL`. | L |
| B2 | **Login rate-limiter** ✅ **done** | `auth.py` (`_check_login_rate`) | Effective limit is **N× looser**; resets on any replica restart. | Redis `INCR` + `EXPIRE` fixed-window (landed): one global counter when `FREE_WEBUI_REDIS_URL` is set, falling back to the in-process sliding window otherwise (and on any Redis hiccup, so logins never lock out). | S |
| B3 | **OIDC first-user→admin provision lock** | `oidc.py:35` (`_provision_lock`) | Process-local `asyncio.Lock` no longer serializes the privilege decision across replicas (race on who becomes first admin). | Postgres `pg_advisory_xact_lock(<const>)` around the count+insert. | M |
| B4 | **Lifespan schema/migration on every boot** | `main.py:161-182` | N replicas run ad-hoc DDL concurrently against one Postgres → races. | Gate migrations behind `pg_advisory_lock` so exactly one replica applies them (or move to out-of-band migrations — §4). | M |

### 3.2 Shared-config prerequisites

| # | Item | Where | Requirement |
| --- | --- | --- | --- |
| B5 | **Signing secret** | `auth.py:20-37`, `config.py:21` | Make `FREE_WEBUI_SECRET_KEY` **mandatory** for multi-replica (fail fast if unset when replicas>1). Inject identically to every replica. |
| B6 | **Plugins dir** | `main.py:176` | Acceptable in-memory **iff** `plugins_dir` is an identical, immutable, shared mount across replicas and rollouts replace all replicas atomically. Document as a deploy invariant. |

### 3.3 Acceptable per-replica (document, don't fix)

| # | Item | Where | Why it's fine |
| --- | --- | --- | --- |
| B7 | **model→connection TTL cache** | `connections.py:137` | Latency optimization with a 20s self-healing TTL → bounded eventual-consistency window. Optionally broadcast invalidation over the same pub/sub. |
| B8 | **code-exec concurrency semaphore** | `code_exec.py:67` | If each replica runs local Docker, a per-replica cap is correct; document that the **cluster** cap is N× and set `code_max_concurrency` accordingly. |
| B9 | **OIDC discovery cache** | `oidc.py:32` | Static doc; N cold-start fetches are negligible. Add a TTL for key rotation. |
| B10 | **per-WebSocket frame-rate deque / revalidate clock** | `channels.py` | Per-connection state on a pinned socket is correct under N replicas. (The per-**user** WS connection cap + presence count are now ✅ **global** via shared Redis counters — `ChannelHub.configure_redis` — with in-process fallback.) |

---

## 4. Key design decisions

**D1 — DB access layer (the pivotal one).** Three options:

1. **Thin dialect abstraction over raw SQL** *(recommended).* A small `Database`
   class owning the connection/pool, normalizing paramstyle at the boundary, and
   centralizing the ~10 dialect-specific snippets (RETURNING, ILIKE, `string_agg`,
   date bucketing, BYTEA, IDENTITY DDL). **Keeps all existing raw SQL** → lowest
   churn, no ORM, no behavior change on SQLite.
2. SQLAlchemy Core (async) — clean dialect handling but a **full rewrite** of
   every query. Disproportionate for a codebase that is deliberately raw-SQL.
3. `encode/databases` — thin, but unmaintained and still needs per-dialect SQL.

→ **Go with (1).** It makes the migration *incremental and reversible*: introduce
the boundary first (no behavior change), then add the Postgres backend behind it.

**D2 — Driver:** `asyncpg` (fast, native `bytes`/`int` round-trips → least churn
for the BLOB/embedding paths). `$N` paramstyle is the most divergent from `?`, so
the abstraction owns the `?`→`$N` rewrite.

**D3 — Migrations:** keep the idempotent `CREATE … IF NOT EXISTS` + additive
`_MIGRATIONS` approach short-term, but **advisory-lock** the step so one replica
applies it; adopt **Alembic** when the schema churn or a destructive migration
demands real versioning. (A real migration framework is already a roadmap item.)

**D4 — Real-time fan-out:** **Redis pub/sub** for channels. It's optional infra —
when `REDIS_URL` is unset, fall back to the in-process hub (single-replica mode),
so SQLite-single-process stays zero-dependency.

**D5 — Object store:** keep blobs in Postgres `BYTEA` first (simplest, already
shared). S3/MinIO is a later optimization for very large media, not a blocker.

**Guiding principle:** **SQLite-single-process must remain the zero-config
default.** Postgres, Redis, and S3 are all *opt-in* via env (`DATABASE_URL`,
`REDIS_URL`, …). Every new backend sits behind a flag with the SQLite path
unchanged.

---

## 5. Phased plan

**Phase 1 — Postgres support (still single replica).** *Valuable on its own*
(durability, backups, bigger datasets, real concurrency) even before multi-replica.
- Introduce the `Database` abstraction (D1) over the *current* aiosqlite
  connection — **no behavior change, suite stays green**. *(first increment, §7)*
- Add the asyncpg backend: per-backend DDL generation, `?`→`$N`, RETURNING,
  ILIKE/`lower()`, `string_agg`, BYTEA, pool. Fix A1–A12.
- Stand up a **dual-backend CI matrix**: run the entire pytest suite against both
  SQLite and a Postgres service container.
- Ship a `sqlite → postgres` data-copy tool (table-by-table dump/load).
- *Effort: large but mostly mechanical, well-guarded by the existing 311 tests.*

**Phase 2 — N stateless replicas.** Requires Phase 1 + Postgres.
- Mandatory shared secret (B5); readiness probe (`SELECT 1` on the pool).
- Redis pub/sub for the ChannelHub + shared per-user WS counter (B1, B10).
- Redis login limiter (B2); `pg_advisory_lock` for OIDC first-admin (B3) and the
  migration step (B4).
- Document the per-replica-acceptable caches (B7–B9) and deploy invariants
  (immutable plugins mount B6; LB with WebSocket support, no sticky sessions
  needed once B1 lands).
- *Effort: medium; each item is independently shippable.*

**Phase 3 — scale-out polish (optional).** S3 object store (D5); Helm chart;
metrics/observability; Alembic (D3).

---

## 6. Risks & testing

- **Behavioral drift between backends** — mitigated by the dual-backend CI matrix
  (the same 311-test suite must pass on both). This is the single most important
  safety net and should land *with* the asyncpg backend, not after.
- **Transaction semantics** — the Postgres backend currently runs **autocommit**
  (one lock-serialized connection), so multi-statement write sequences are not
  atomic: an adversarial review confirmed that `set_conversation_collections` /
  `_set_preset_collections` (DELETE-then-INSERT) and the edit/delete/regenerate
  truncation (DELETE + `gc_orphan_files` + UPDATE) can leave partial state if a
  statement fails mid-sequence — destructive-then-non-atomic. (SQLite's shared
  connection has a smaller version of the same window.) **The fix is the
  per-request connection pool + an explicit transaction per request** (the
  Phase-1.5 refinement) — wrap each handler's writes in one transaction that
  `commit()` commits. Until then these sequences are best-effort on Postgres.
- **Migration coordination** — never run ad-hoc DDL from N replicas (B4).
- **No SQLite regression** — the zero-config SQLite path must stay byte-for-byte
  behaviorally identical; every phase keeps it as the default.

---

## 7. Recommended first increment (small, mergeable, no behavior change)

Introduce the **DB abstraction boundary** while still backed by SQLite:

1. A `Database` wrapper exposing `execute()`, `fetch_one()`, `fetch_all()`,
   `insert_returning()` (the last centralizes the `lastrowid`→`RETURNING` pattern),
   and `transaction()`, plus a `dialect` flag — initially just delegating to the
   existing `aiosqlite` connection.
2. Migrate call sites to it **incrementally** (mechanical, reviewable in slices),
   keeping the suite green at every step.
3. Generate `SCHEMA`/`INDEXES`/types per-dialect so adding the asyncpg backend
   later is purely additive.

This de-risks the whole effort: it turns a scary big-bang port into a sequence of
no-op refactors, after which the Postgres backend is a contained addition behind
the boundary — with the SQLite default never breaking.
