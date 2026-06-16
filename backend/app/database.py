"""Backend-agnostic database boundary (Phase 1 of docs/SCALING.md).

The whole app talks to a ``Database`` instead of a raw ``aiosqlite`` connection.
Today it transparently delegates to aiosqlite (``dialect="sqlite"``) with **no
behavior change** — every existing ``db.execute(...)`` / ``db.commit()`` call
keeps working via passthrough, and anything not wrapped is forwarded to the
underlying connection. This establishes the seam so a Postgres (asyncpg) backend
can later slot in behind the same interface, with the dialect-specific bits
(``?``→``$N`` paramstyle, ``RETURNING`` for inserts, date bucketing, ``ILIKE``,
``BYTEA``, per-backend DDL) centralized here rather than scattered across ~30
modules.

Call sites migrate incrementally to the convenience helpers below
(``fetch_one`` / ``fetch_all`` / ``fetch_val`` / ``insert``); ``insert`` in
particular centralizes the ``cursor.lastrowid`` pattern that Postgres replaces
with ``RETURNING id``.
"""
from __future__ import annotations

from typing import Any

import aiosqlite

Row = Any  # a tuple-like row (positional indexing), as today

# Param sequence accepted by the DB-API layer.
Params = "tuple | list"


class Database:
    """Thin wrapper over a DB connection. SQLite-only for now; the public surface
    is what the rest of the app already uses plus a few helpers."""

    def __init__(self, conn: aiosqlite.Connection, dialect: str = "sqlite") -> None:
        self._conn = conn
        self.dialect = dialect

    # ---- raw passthrough: existing call sites use these unchanged ----

    async def execute(self, sql: str, params: "tuple | list" = ()) -> aiosqlite.Cursor:
        return await self._conn.execute(sql, params)

    async def executemany(self, sql: str, seq_of_params) -> aiosqlite.Cursor:
        return await self._conn.executemany(sql, seq_of_params)

    async def executescript(self, script: str) -> Any:
        return await self._conn.executescript(script)

    async def commit(self) -> None:
        await self._conn.commit()

    async def rollback(self) -> None:
        await self._conn.rollback()

    async def close(self) -> None:
        await self._conn.close()

    def __getattr__(self, name: str) -> Any:
        # Forward anything not explicitly wrapped (e.g. row_factory) to the
        # underlying connection so this stays a transparent proxy mid-migration.
        if name == "_conn":
            raise AttributeError(name)
        return getattr(self._conn, name)

    # ---- convenience helpers: call sites migrate to these incrementally ----

    async def fetch_one(self, sql: str, params: "tuple | list" = ()) -> Row | None:
        cur = await self._conn.execute(sql, params)
        return await cur.fetchone()

    async def fetch_all(self, sql: str, params: "tuple | list" = ()) -> list[Row]:
        cur = await self._conn.execute(sql, params)
        return list(await cur.fetchall())

    async def fetch_val(
        self, sql: str, params: "tuple | list" = (), default: Any = None
    ) -> Any:
        row = await self.fetch_one(sql, params)
        return row[0] if row is not None else default

    async def insert(self, sql: str, params: "tuple | list" = ()) -> int:
        """Run an INSERT and return the new row's autoincrement id.

        Centralizes the ``cursor.lastrowid`` pattern so the Postgres backend can
        switch to ``... RETURNING id`` in one place instead of 16 call sites.
        """
        cur = await self._conn.execute(sql, params)
        return int(cur.lastrowid)
