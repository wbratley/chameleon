import aiosqlite

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    message      BLOB    NOT NULL,
    delivered    INTEGER NOT NULL DEFAULT 0,
    delivered_at TEXT    DEFAULT NULL
)
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_messages_delivered ON messages (delivered, id)
"""


class MessageQueue:
    def __init__(self, db_path: str, retain_minutes: int = 30) -> None:
        self._db_path = db_path
        self._retain_minutes = retain_minutes
        self._conn: aiosqlite.Connection | None = None

    async def setup(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        await self._conn.execute(_CREATE_TABLE)
        await self._conn.execute(_CREATE_INDEX)
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

    async def pending(self) -> list[tuple[int, bytes]]:
        cur = await self._conn.execute(
            "SELECT id, message FROM messages WHERE delivered = 0 ORDER BY id"
        )
        return list(await cur.fetchall())

    async def ack(self, msg_id: int) -> None:
        await self._conn.execute(
            "UPDATE messages "
            "SET delivered = 1, delivered_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
            "WHERE id = ?",
            (msg_id,),
        )
        await self._conn.commit()

    async def sweep(self) -> int:
        cur = await self._conn.execute(
            f"DELETE FROM messages "
            f"WHERE (delivered = 1 "
            f"       AND delivered_at < strftime('%Y-%m-%dT%H:%M:%fZ','now','-{self._retain_minutes} minutes')) "
            f"   OR (delivered = 0 "
            f"       AND received_at < strftime('%Y-%m-%dT%H:%M:%fZ','now','-24 hours'))",
            # f-string is safe: retain_minutes is an int validated by pydantic
        )
        await self._conn.commit()
        return cur.rowcount
