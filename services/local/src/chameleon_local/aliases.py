import random
import re
import string
from dataclasses import dataclass

import aiosqlite

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS aliases (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    address          TEXT    NOT NULL UNIQUE,
    service          TEXT    NOT NULL,
    created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    burned           INTEGER NOT NULL DEFAULT 0,
    burned_at        TEXT    DEFAULT NULL,
    email_count      INTEGER NOT NULL DEFAULT 0,
    last_received_at TEXT    DEFAULT NULL
)
"""

_CHARS = string.ascii_lowercase + string.digits  # a-z0-9


@dataclass
class Alias:
    id: int
    address: str
    service: str
    created_at: str
    burned: bool
    burned_at: str | None
    email_count: int
    last_received_at: str | None


def _make_slug(service: str) -> str:
    slug = service.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")[:20]
    return slug or "alias"


def _row_to_alias(row: aiosqlite.Row) -> Alias:
    return Alias(
        id=row["id"],
        address=row["address"],
        service=row["service"],
        created_at=row["created_at"],
        burned=bool(row["burned"]),
        burned_at=row["burned_at"],
        email_count=row["email_count"],
        last_received_at=row["last_received_at"],
    )


class AliasDB:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def setup(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.execute(_CREATE_TABLE)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def create(self, service: str, domain: str) -> Alias:
        slug = _make_slug(service)
        # Store the domain lowercased: inbound recipients are lowercased before
        # lookup (see client._extract_to), so a mixed-case MY_DOMAIN would
        # otherwise never match and every delivery would look like an unknown alias.
        domain = domain.lower()
        for _ in range(10):
            suffix = "".join(random.choices(_CHARS, k=4))
            address = f"{slug}-{suffix}@{domain}"
            try:
                cur = await self._conn.execute(
                    "INSERT INTO aliases (address, service) VALUES (?, ?)",
                    (address, service),
                )
                await self._conn.commit()
                row = await (
                    await self._conn.execute(
                        "SELECT * FROM aliases WHERE id = ?", (cur.lastrowid,)
                    )
                ).fetchone()
                return _row_to_alias(row)
            except aiosqlite.IntegrityError:
                continue
        raise RuntimeError(f"Could not generate unique alias for {service!r}")

    async def all(self) -> list[Alias]:
        cur = await self._conn.execute("SELECT * FROM aliases ORDER BY id DESC")
        return [_row_to_alias(r) for r in await cur.fetchall()]

    async def burn(self, alias_id: int) -> Alias | None:
        await self._conn.execute(
            "UPDATE aliases "
            "SET burned = 1, burned_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
            "WHERE id = ? AND burned = 0",
            (alias_id,),
        )
        await self._conn.commit()
        row = await (
            await self._conn.execute(
                "SELECT * FROM aliases WHERE id = ?", (alias_id,)
            )
        ).fetchone()
        return _row_to_alias(row) if row else None

    async def record_delivery(self, address: str) -> bool:
        """Update stats for address. Returns False if burned (message should be dropped)."""
        row = await (
            await self._conn.execute(
                "SELECT id, burned FROM aliases WHERE address = ?", (address,)
            )
        ).fetchone()

        if row is None:
            await self._conn.execute(
                "INSERT INTO aliases (address, service, email_count, last_received_at) "
                "VALUES (?, ?, 1, strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                (address, address.split("@")[0]),
            )
            await self._conn.commit()
            return True

        if row["burned"]:
            return False

        await self._conn.execute(
            "UPDATE aliases SET email_count = email_count + 1, "
            "last_received_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
            "WHERE id = ?",
            (row["id"],),
        )
        await self._conn.commit()
        return True
