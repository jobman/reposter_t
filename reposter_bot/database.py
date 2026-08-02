from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite


@dataclass(frozen=True, slots=True)
class MediaRecord:
    kind: str
    file_id: str


@dataclass(frozen=True, slots=True)
class QueueItem:
    id: int
    media: tuple[MediaRecord, ...]
    attempts: int


def utc_now_text() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = await aiosqlite.connect(self.path)
        self.connection.row_factory = aiosqlite.Row
        await self.connection.execute("PRAGMA foreign_keys = ON")
        await self.connection.execute("PRAGMA journal_mode = WAL")
        await self.connection.execute("PRAGMA busy_timeout = 5000")
        await self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS queue_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_chat_id INTEGER NOT NULL,
                source_message_id INTEGER,
                media_group_id TEXT,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued'
                    CHECK(status IN ('queued', 'publishing', 'published')),
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                published_at TEXT
            );

            CREATE UNIQUE INDEX IF NOT EXISTS uq_queue_single_source
                ON queue_items(source_chat_id, source_message_id)
                WHERE media_group_id IS NULL;
            CREATE UNIQUE INDEX IF NOT EXISTS uq_queue_album_source
                ON queue_items(source_chat_id, media_group_id)
                WHERE media_group_id IS NOT NULL;

            CREATE TABLE IF NOT EXISTS queue_media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL REFERENCES queue_items(id) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                kind TEXT NOT NULL,
                file_id TEXT NOT NULL,
                UNIQUE(item_id, position)
            );

            CREATE TABLE IF NOT EXISTS pending_media (
                source_chat_id INTEGER NOT NULL,
                source_message_id INTEGER NOT NULL,
                media_group_id TEXT NOT NULL,
                sender_user_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                file_id TEXT NOT NULL,
                received_at TEXT NOT NULL,
                PRIMARY KEY(source_chat_id, source_message_id)
            );

            CREATE TABLE IF NOT EXISTS schedule_runs (
                slot_key TEXT PRIMARY KEY,
                scheduled_for TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('claimed', 'published', 'failed')),
                item_id INTEGER REFERENCES queue_items(id),
                error TEXT,
                completed_at TEXT
            );
            """
        )
        await self.connection.execute(
            "UPDATE queue_items SET status = 'queued' WHERE status = 'publishing'"
        )
        await self.connection.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES ('paused', 'true')"
        )
        await self.connection.commit()

    async def close(self) -> None:
        if self.connection is not None:
            await self.connection.close()
            self.connection = None

    def _connection(self) -> aiosqlite.Connection:
        if self.connection is None:
            raise RuntimeError("Database is not open")
        return self.connection

    async def get_setting(self, key: str) -> str | None:
        cursor = await self._connection().execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return str(row["value"]) if row else None

    async def set_setting(self, key: str, value: str) -> None:
        async with self._write_lock:
            await self._connection().execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            await self._connection().commit()

    async def claim_owner(self, user_id: int) -> bool:
        async with self._write_lock:
            cursor = await self._connection().execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES ('owner_user_id', ?)",
                (str(user_id),),
            )
            await self._connection().commit()
            return cursor.rowcount == 1

    async def is_owner(self, user_id: int) -> bool:
        return await self.get_setting("owner_user_id") == str(user_id)

    async def add_single(
        self, source_chat_id: int, source_message_id: int, kind: str, file_id: str
    ) -> tuple[int, bool]:
        async with self._write_lock:
            connection = self._connection()
            cursor = await connection.execute(
                """
                INSERT OR IGNORE INTO queue_items(
                    source_chat_id, source_message_id, created_at, status
                ) VALUES (?, ?, ?, 'queued')
                """,
                (source_chat_id, source_message_id, utc_now_text()),
            )
            created = cursor.rowcount == 1
            if created:
                item_id = int(cursor.lastrowid)
                await connection.execute(
                    "INSERT INTO queue_media(item_id, position, kind, file_id) VALUES (?, 0, ?, ?)",
                    (item_id, kind, file_id),
                )
            else:
                existing = await connection.execute(
                    "SELECT id FROM queue_items WHERE source_chat_id = ? AND source_message_id = ? "
                    "AND media_group_id IS NULL",
                    (source_chat_id, source_message_id),
                )
                row = await existing.fetchone()
                await existing.close()
                if row is None:
                    raise RuntimeError("Unable to find duplicate queue item")
                item_id = int(row["id"])
            await connection.commit()
            return item_id, created

    async def add_album_part(
        self,
        source_chat_id: int,
        source_message_id: int,
        media_group_id: str,
        sender_user_id: int,
        kind: str,
        file_id: str,
    ) -> None:
        async with self._write_lock:
            await self._connection().execute(
                """
                INSERT OR IGNORE INTO pending_media(
                    source_chat_id, source_message_id, media_group_id, sender_user_id,
                    kind, file_id, received_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_chat_id,
                    source_message_id,
                    media_group_id,
                    sender_user_id,
                    kind,
                    file_id,
                    utc_now_text(),
                ),
            )
            await self._connection().commit()

    async def finalize_pending_albums(self, cutoff: datetime) -> list[tuple[int, int, int]]:
        """Return (item_id, source_chat_id, media_count) for newly finalized albums."""
        async with self._write_lock:
            connection = self._connection()
            groups_cursor = await connection.execute(
                """
                SELECT source_chat_id, media_group_id
                FROM pending_media
                GROUP BY source_chat_id, media_group_id
                HAVING MAX(received_at) <= ?
                """,
                (cutoff.isoformat(),),
            )
            groups = await groups_cursor.fetchall()
            await groups_cursor.close()
            finalized: list[tuple[int, int, int]] = []

            for group in groups:
                chat_id = int(group["source_chat_id"])
                group_id = str(group["media_group_id"])
                parts_cursor = await connection.execute(
                    """
                    SELECT source_message_id, kind, file_id
                    FROM pending_media
                    WHERE source_chat_id = ? AND media_group_id = ?
                    ORDER BY source_message_id
                    """,
                    (chat_id, group_id),
                )
                parts = await parts_cursor.fetchall()
                await parts_cursor.close()
                if not parts:
                    continue

                insert_cursor = await connection.execute(
                    """
                    INSERT OR IGNORE INTO queue_items(
                        source_chat_id, source_message_id, media_group_id, created_at, status
                    ) VALUES (?, ?, ?, ?, 'queued')
                    """,
                    (chat_id, int(parts[0]["source_message_id"]), group_id, utc_now_text()),
                )
                if insert_cursor.rowcount == 1:
                    item_id = int(insert_cursor.lastrowid)
                    await connection.executemany(
                        "INSERT INTO queue_media(item_id, position, kind, file_id) "
                        "VALUES (?, ?, ?, ?)",
                        [
                            (item_id, position, str(part["kind"]), str(part["file_id"]))
                            for position, part in enumerate(parts)
                        ],
                    )
                    finalized.append((item_id, chat_id, len(parts)))
                await connection.execute(
                    "DELETE FROM pending_media WHERE source_chat_id = ? AND media_group_id = ?",
                    (chat_id, group_id),
                )

            await connection.commit()
            return finalized

    async def queue_count(self) -> int:
        cursor = await self._connection().execute(
            "SELECT COUNT(*) AS count FROM queue_items WHERE status = 'queued'"
        )
        row = await cursor.fetchone()
        await cursor.close()
        return int(row["count"])

    async def list_queue(self, limit: int = 20) -> list[dict[str, object]]:
        cursor = await self._connection().execute(
            """
            SELECT qi.id, qi.created_at, qi.attempts, COUNT(qm.id) AS media_count,
                   GROUP_CONCAT(DISTINCT qm.kind) AS kinds
            FROM queue_items qi
            JOIN queue_media qm ON qm.item_id = qi.id
            WHERE qi.status = 'queued'
            GROUP BY qi.id
            ORDER BY qi.id
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    async def remove_queued(self, item_id: int) -> bool:
        async with self._write_lock:
            cursor = await self._connection().execute(
                "DELETE FROM queue_items WHERE id = ? AND status = 'queued'", (item_id,)
            )
            await self._connection().commit()
            return cursor.rowcount == 1

    async def claim_next_item(self) -> QueueItem | None:
        async with self._write_lock:
            connection = self._connection()
            cursor = await connection.execute(
                "SELECT id, attempts FROM queue_items WHERE status = 'queued' ORDER BY id LIMIT 1"
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                return None
            item_id = int(row["id"])
            changed = await connection.execute(
                "UPDATE queue_items SET status = 'publishing' WHERE id = ? AND status = 'queued'",
                (item_id,),
            )
            if changed.rowcount != 1:
                await connection.rollback()
                return None
            media_cursor = await connection.execute(
                "SELECT kind, file_id FROM queue_media WHERE item_id = ? ORDER BY position",
                (item_id,),
            )
            media_rows = await media_cursor.fetchall()
            await media_cursor.close()
            await connection.commit()
            return QueueItem(
                id=item_id,
                attempts=int(row["attempts"]),
                media=tuple(
                    MediaRecord(kind=str(media["kind"]), file_id=str(media["file_id"]))
                    for media in media_rows
                ),
            )

    async def mark_published(self, item_id: int) -> None:
        async with self._write_lock:
            await self._connection().execute(
                "UPDATE queue_items SET status = 'published', published_at = ?, last_error = NULL "
                "WHERE id = ?",
                (utc_now_text(), item_id),
            )
            await self._connection().commit()

    async def mark_publish_failed(self, item_id: int, error: str) -> None:
        async with self._write_lock:
            await self._connection().execute(
                "UPDATE queue_items SET status = 'queued', attempts = attempts + 1, last_error = ? "
                "WHERE id = ?",
                (error[:1000], item_id),
            )
            await self._connection().commit()

    async def claim_slot(self, slot_key: str, scheduled_for: datetime) -> bool:
        async with self._write_lock:
            cursor = await self._connection().execute(
                "INSERT OR IGNORE INTO schedule_runs(slot_key, scheduled_for, status) "
                "VALUES (?, ?, 'claimed')",
                (slot_key, scheduled_for.isoformat()),
            )
            await self._connection().commit()
            return cursor.rowcount == 1

    async def finish_slot(
        self, slot_key: str, status: str, item_id: int | None, error: str | None = None
    ) -> None:
        if status not in {"published", "failed"}:
            raise ValueError("Invalid slot status")
        async with self._write_lock:
            await self._connection().execute(
                "UPDATE schedule_runs SET status = ?, item_id = ?, error = ?, completed_at = ? "
                "WHERE slot_key = ?",
                (status, item_id, error[:1000] if error else None, utc_now_text(), slot_key),
            )
            await self._connection().commit()

    async def diagnostics(self) -> str:
        cursor = await self._connection().execute(
            "SELECT status, COUNT(*) AS count FROM queue_items GROUP BY status ORDER BY status"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return json.dumps({str(row["status"]): int(row["count"]) for row in rows})
