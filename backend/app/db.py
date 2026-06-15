from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',
    -- Bumped to revoke a user's live sessions (password reset, logout-everywhere).
    token_version INTEGER NOT NULL DEFAULT 0,
    created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id            TEXT PRIMARY KEY,
    user_id       INTEGER REFERENCES users(id) ON DELETE CASCADE,
    title         TEXT NOT NULL DEFAULT 'new chat',
    model         TEXT,
    system_prompt TEXT,
    temperature   REAL,
    top_p         REAL,
    stop          TEXT,
    web_search    INTEGER NOT NULL DEFAULT 0,
    tools_enabled INTEGER NOT NULL DEFAULT 0,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    -- Branching: parent_id links a regenerated assistant variant to the
    -- variant it replaced; active=0 marks a superseded variant (kept, not
    -- deleted, so regenerate is non-destructive). Reads filter active=1.
    parent_id       INTEGER,
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS message_feedback (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    rating     INTEGER NOT NULL,
    comment    TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE (message_id, user_id)
);

CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    filename        TEXT NOT NULL,
    mime            TEXT,
    bytes           INTEGER NOT NULL,
    chunk_count     INTEGER NOT NULL DEFAULT 0,
    embedding_model TEXT,
    created_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,
    text         TEXT NOT NULL,
    embedding    BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS prompts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS presets (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name           TEXT NOT NULL,
    model          TEXT,
    system_prompt  TEXT,
    temperature    REAL,
    top_p          REAL,
    stop           TEXT,
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    key_prefix   TEXT NOT NULL,
    key_hash     TEXT NOT NULL UNIQUE,
    last_used_at INTEGER,
    created_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content    TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS mcp_servers (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    url        TEXT NOT NULL,
    headers    TEXT,
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS groups (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT UNIQUE NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS group_members (
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, user_id)
);

-- A model with NO rows here is public (everyone). With rows, only the listed
-- users + members of the listed groups (and admins) may see/use it.
CREATE TABLE IF NOT EXISTS model_access (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id TEXT NOT NULL,
    group_id INTEGER REFERENCES groups(id) ON DELETE CASCADE,
    user_id  INTEGER REFERENCES users(id) ON DELETE CASCADE,
    -- a grant must name a group or a user (never both-null, which would mark a
    -- model 'restricted' while granting access to no one)
    CHECK (group_id IS NOT NULL OR user_id IS NOT NULL)
);
"""

# Indexes are created AFTER _ensure_columns so an index on a migrated column
# (e.g. conversations.user_id, added to a legacy pre-auth table) does not fail
# with "no such column" when an old DB is opened by a newer build.
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_documents_conv ON documents(conversation_id);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_prompts_user ON prompts(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_presets_user ON presets(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_mcp_servers_user ON mcp_servers(user_id, enabled);
CREATE INDEX IF NOT EXISTS idx_messages_active ON messages(conversation_id, active, id);
CREATE INDEX IF NOT EXISTS idx_feedback_message ON message_feedback(message_id);
CREATE INDEX IF NOT EXISTS idx_group_members_user ON group_members(user_id);
CREATE INDEX IF NOT EXISTS idx_model_access_model ON model_access(model_id);
"""

_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("conversations", "system_prompt", "TEXT"),
    ("conversations", "temperature", "REAL"),
    ("conversations", "top_p", "REAL"),
    ("conversations", "stop", "TEXT"),
    ("conversations", "user_id", "INTEGER REFERENCES users(id) ON DELETE CASCADE"),
    ("conversations", "web_search", "INTEGER NOT NULL DEFAULT 0"),
    ("conversations", "tools_enabled", "INTEGER NOT NULL DEFAULT 0"),
    ("messages", "parent_id", "INTEGER"),
    ("messages", "active", "INTEGER NOT NULL DEFAULT 1"),
    ("users", "token_version", "INTEGER NOT NULL DEFAULT 0"),
)


async def _ensure_columns(conn: aiosqlite.Connection) -> None:
    for table, column, decl in _MIGRATIONS:
        cur = await conn.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in await cur.fetchall()}
        if column not in existing:
            await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    await conn.commit()


async def open_db(path: str) -> aiosqlite.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(path)
    await conn.execute("PRAGMA foreign_keys = ON")
    await conn.execute("PRAGMA journal_mode = WAL")
    # Wait up to 5s for a competing writer instead of failing instantly with
    # "database is locked" (single shared connection + WAL under concurrency).
    await conn.execute("PRAGMA busy_timeout = 5000")
    await conn.executescript(SCHEMA)
    await conn.commit()
    # Add any columns missing from a legacy DB BEFORE creating indexes that
    # reference them.
    await _ensure_columns(conn)
    await conn.executescript(INDEXES)
    await conn.commit()
    return conn
