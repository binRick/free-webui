"""The backend-agnostic Database boundary (Phase 1 of docs/SCALING.md)."""
import aiosqlite

from app.database import Database


async def _mem_db() -> Database:
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)")
    return Database(conn, dialect="sqlite")


async def test_insert_returns_new_id():
    db = await _mem_db()
    assert await db.insert("INSERT INTO t (name) VALUES (?)", ("alice",)) == 1
    assert await db.insert("INSERT INTO t (name) VALUES (?)", ("bob",)) == 2
    await db.close()


async def test_fetch_helpers():
    db = await _mem_db()
    await db.insert("INSERT INTO t (name) VALUES (?)", ("alice",))
    await db.insert("INSERT INTO t (name) VALUES (?)", ("bob",))

    assert (await db.fetch_one("SELECT name FROM t WHERE id = ?", (1,)))[0] == "alice"
    assert [r[0] for r in await db.fetch_all("SELECT name FROM t ORDER BY id")] == ["alice", "bob"]
    assert await db.fetch_val("SELECT COUNT(*) FROM t") == 2
    assert await db.fetch_one("SELECT name FROM t WHERE id = ?", (99,)) is None
    assert await db.fetch_val("SELECT name FROM t WHERE id = ?", (99,), default="x") == "x"
    await db.close()


def test_integrity_errors_cover_both_backends():
    """`except INTEGRITY_ERRORS` must catch a UNIQUE/FK violation on either
    backend — on Postgres asyncpg raises a non-sqlite exception that the old
    `except aiosqlite.IntegrityError` silently missed (turning OIDC race
    recovery into a 500)."""
    import sqlite3

    from app.database import INTEGRITY_ERRORS

    try:
        raise sqlite3.IntegrityError("dup")
    except INTEGRITY_ERRORS:
        pass  # SQLite path caught

    try:
        import asyncpg
    except ImportError:
        return
    try:
        raise asyncpg.exceptions.UniqueViolationError("dup")
    except INTEGRITY_ERRORS:
        pass  # Postgres path now caught too


async def test_passthrough_and_proxy():
    db = await _mem_db()
    assert db.dialect == "sqlite"
    # raw passthrough still works exactly like the underlying connection
    cur = await db.execute("INSERT INTO t (name) VALUES (?)", ("carol",))
    assert cur.lastrowid == 1
    await db.commit()
    await db.executemany("INSERT INTO t (name) VALUES (?)", [("d",), ("e",)])
    assert await db.fetch_val("SELECT COUNT(*) FROM t") == 3
    # __getattr__ forwards unknown attributes to the connection (no AttributeError)
    _ = db.in_transaction
    await db.close()
