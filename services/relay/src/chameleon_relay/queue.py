from collections.abc import AsyncIterator

import aiosqlite

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    message     BLOB    NOT NULL
)
"""


class MessageQueue:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def setup(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        await self._conn.execute("PRAGMA secure_delete = ON")
        await self._conn.execute("PRAGMA journal_mode = DELETE")
        await self._conn.execute(_CREATE_TABLE)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def enqueue(self, message: bytes) -> int:
        cur = await self._conn.execute(
            "INSERT INTO messages (message) VALUES (?)", (message,)
        )
        await self._conn.commit()
        return cur.lastrowid

    async def pending(self) -> AsyncIterator[tuple[int, bytes]]:
        """Stream pending messages in insertion order, one at a time.

        Snapshot semantics: only rows that existed when iteration began are
        yielded (id <= the high-water mark captured up front). Rows enqueued
        mid-replay are excluded — they are delivered by the live broadcast
        path, so a message is never delivered twice on one connection.

        Rows are fetched via short keyset queries (rowid seek) rather than one
        long-lived cursor: aiosqlite dispatches each fetch to its worker
        thread, so the caller's awaited sends (ws.send_json) let concurrent
        enqueue/ack/sweep writes interleave with an open cursor — and sqlite3
        leaves visited rows undefined when the table is modified mid-scan.
        Each query here is atomic and self-contained, so interleaving is safe,
        and memory stays bounded at a single message regardless of backlog
        size (issue #8).
        """
        async with self._conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM messages"
        ) as cur:
            (max_id,) = await cur.fetchone()
        last_id = 0
        while True:
            async with self._conn.execute(
                "SELECT id, message FROM messages "
                "WHERE id > ? AND id <= ? ORDER BY id LIMIT 1",
                (last_id, max_id),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return
            last_id = row[0]
            yield row

    async def ack(self, msg_id: int) -> None:
        await self._conn.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
        await self._conn.commit()

    async def sweep(self, retain_minutes: int) -> int:
        cur = await self._conn.execute(
            "DELETE FROM messages "
            "WHERE received_at < strftime('%Y-%m-%dT%H:%M:%fZ','now',?)",
            (f"-{retain_minutes} minutes",),
        )
        await self._conn.commit()
        return cur.rowcount
