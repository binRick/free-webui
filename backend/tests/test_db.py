"""db.open_db: busy_timeout pragma, FK cascade, additive migration, idempotency.

These exercise the only upgrade path for existing installs (the additive
_ensure_columns migration) and the integrity backbone (foreign-key cascade)."""
import aiosqlite


async def test_open_db_sets_busy_timeout(tmp_path):
    from app.db import open_db

    db = await open_db(str(tmp_path / "t.db"))
    try:
        cur = await db.execute("PRAGMA busy_timeout")
        assert (await cur.fetchone())[0] >= 1000
    finally:
        await db.close()


async def test_foreign_key_cascade(tmp_path):
    from app.db import open_db

    db = await open_db(str(tmp_path / "t.db"))
    try:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, role, created_at) "
            "VALUES (1, 'u', 'h', 'user', 1)"
        )
        await db.execute(
            "INSERT INTO conversations (id, user_id, title, created_at, updated_at) "
            "VALUES ('c', 1, 't', 1, 1)"
        )
        await db.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) "
            "VALUES ('c', 'user', 'hi', 1)"
        )
        await db.commit()

        await db.execute("DELETE FROM users WHERE id = 1")
        await db.commit()

        convs = (await (await db.execute("SELECT COUNT(*) FROM conversations")).fetchone())[0]
        msgs = (await (await db.execute("SELECT COUNT(*) FROM messages")).fetchone())[0]
        assert convs == 0 and msgs == 0  # cascade fired through both levels
    finally:
        await db.close()


async def test_open_db_idempotent_preserves_rows(tmp_path):
    from app.db import open_db

    path = str(tmp_path / "t.db")
    db = await open_db(path)
    await db.execute(
        "INSERT INTO users (id, username, password_hash, role, created_at) "
        "VALUES (1, 'u', 'h', 'user', 1)"
    )
    await db.commit()
    await db.close()

    db2 = await open_db(path)  # CREATE IF NOT EXISTS + migrations must not wipe data
    try:
        n = (await (await db2.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        assert n == 1
    finally:
        await db2.close()


async def test_migration_adds_missing_columns_without_data_loss(tmp_path):
    from app.db import open_db

    path = str(tmp_path / "legacy.db")
    # Simulate a pre-migration install: conversations without the newer columns.
    conn = await aiosqlite.connect(path)
    await conn.execute(
        "CREATE TABLE conversations ("
        "  id TEXT PRIMARY KEY,"
        "  title TEXT NOT NULL DEFAULT 'new chat',"
        "  model TEXT,"
        "  created_at INTEGER NOT NULL,"
        "  updated_at INTEGER NOT NULL)"
    )
    await conn.execute(
        "INSERT INTO conversations (id, title, created_at, updated_at) "
        "VALUES ('c', 'legacy chat', 1, 1)"
    )
    await conn.commit()
    await conn.close()

    db = await open_db(path)
    try:
        cur = await db.execute("PRAGMA table_info(conversations)")
        cols = {r[1] for r in await cur.fetchall()}
        for c in (
            "system_prompt", "temperature", "top_p", "stop",
            "user_id", "web_search", "tools_enabled",
        ):
            assert c in cols, f"migration did not add column {c!r}"
        row = await (await db.execute("SELECT title FROM conversations WHERE id = 'c'")).fetchone()
        assert row[0] == "legacy chat"  # existing data survived
    finally:
        await db.close()
