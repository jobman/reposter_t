import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from reposter_bot.database import Database, MediaRecord


@pytest.mark.asyncio
async def test_open_migrates_legacy_queue_schema(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE queue_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_chat_id INTEGER NOT NULL,
            source_message_id INTEGER,
            media_group_id TEXT,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            published_at TEXT
        );
        CREATE TABLE queue_media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL REFERENCES queue_items(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            kind TEXT NOT NULL,
            file_id TEXT NOT NULL,
            UNIQUE(item_id, position)
        );
        """
    )
    connection.close()

    database = Database(path)
    await database.open()
    try:
        db = database._connection()
        queue_item_columns = {
            row["name"]
            for row in await (await db.execute("PRAGMA table_info(queue_items)")).fetchall()
        }
        queue_media_columns = {
            row["name"]
            for row in await (await db.execute("PRAGMA table_info(queue_media)")).fetchall()
        }
        assert {"is_suggestion", "text_content"} <= queue_item_columns
        assert "local_path" in queue_media_columns
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_single_media_queue_and_deduplication(tmp_path) -> None:
    database = Database(tmp_path / "queue.sqlite3")
    await database.open()
    try:
        item_id, created = await database.add_single(1, 10, "photo", "file-a")
        duplicate_id, duplicate_created = await database.add_single(1, 10, "photo", "file-a")
        assert created is True
        assert duplicate_created is False
        assert duplicate_id == item_id
        assert await database.queue_count() == 1

        item = await database.claim_next_item()
        assert item is not None
        assert item.id == item_id
        assert item.media[0].file_id == "file-a"
        await database.mark_published(item.id)
        assert await database.queue_count() == 0
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_album_is_finalized_as_one_queue_item(tmp_path) -> None:
    database = Database(tmp_path / "queue.sqlite3")
    await database.open()
    try:
        await database.add_album_part(1, 20, "group", 7, "photo", "one")
        await database.add_album_part(1, 21, "group", 7, "video", "two")
        finalized = await database.finalize_pending_albums(datetime.now(UTC) + timedelta(seconds=1))
        assert len(finalized) == 1
        assert finalized[0][2] == 2
        item = await database.claim_next_item()
        assert item is not None
        assert [media.file_id for media in item.media] == ["one", "two"]
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_approved_text_suggestion_enters_shared_queue(tmp_path) -> None:
    database = Database(tmp_path / "queue.sqlite3")
    await database.open()
    try:
        suggestion_id = await database.create_suggestion(
            submitter_user_id=42,
            submitter_chat_id=42,
            submitter_username="author",
            submitter_name="Author",
            source_message_id=100,
            source_text="Предложенный текст",
            media=None,
        )
        assert await database.begin_suggestion_approval(suggestion_id) is True
        queue_item_id = await database.enqueue_approved_suggestion(suggestion_id, ())

        item = await database.claim_next_item()
        assert item is not None
        assert item.id == queue_item_id
        assert item.is_suggestion is True
        assert item.text_content == "Предложенный текст"
        assert item.media == ()
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_decline_reason_is_bound_to_force_reply_prompt(tmp_path) -> None:
    database = Database(tmp_path / "queue.sqlite3")
    await database.open()
    try:
        suggestion_id = await database.create_suggestion(
            submitter_user_id=42,
            submitter_chat_id=42,
            submitter_username=None,
            submitter_name="Author",
            source_message_id=101,
            source_text=None,
            media=MediaRecord(kind="photo", file_id="photo-id"),
        )
        assert await database.begin_suggestion_decline(suggestion_id) is True
        await database.set_suggestion_reason_prompt(suggestion_id, 555)
        suggestion = await database.suggestion_awaiting_prompt(555)
        assert suggestion is not None
        assert suggestion.id == suggestion_id
        assert await database.mark_suggestion_declined(suggestion_id, "Не подходит") is True
        assert await database.suggestion_awaiting_prompt(555) is None
    finally:
        await database.close()
