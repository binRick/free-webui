from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',
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
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id, updated_at DESC);
"""

_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("conversations", "system_prompt", "TEXT"),
    ("conversations", "temperature", "REAL"),
    ("conversations", "top_p", "REAL"),
    ("conversations", "stop", "TEXT"),
    ("conversations", "user_id", "INTEGER REFERENCES users(id) ON DELETE CASCADE"),
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
    await conn.executescript(SCHEMA)
    await conn.commit()
    await _ensure_columns(conn)
    return conn
