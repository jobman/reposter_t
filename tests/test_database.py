from datetime import UTC, datetime, timedelta

import pytest

from reposter_bot.database import Database


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
