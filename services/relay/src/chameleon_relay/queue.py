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

    async def pending(self) -> list[tuple[int, bytes]]:
        cur = await self._conn.execute(
            "SELECT id, message FROM messages ORDER BY id"
        )
        return list(await cur.fetchall())

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
