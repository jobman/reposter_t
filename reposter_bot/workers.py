from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aiogram import Bot

from reposter_bot.config import Settings, parse_clock
from reposter_bot.database import Database, QueueItem
from reposter_bot.publisher import Publisher
from reposter_bot.scheduling import ScheduleConfig, active_slot

logger = logging.getLogger(__name__)


def cleanup_local_media(item: QueueItem) -> None:
    parents: set[Path] = set()
    for media in item.media:
        if media.local_path:
            path = Path(media.local_path)
            path.unlink(missing_ok=True)
            parents.add(path.parent)
    for parent in parents:
        try:
            parent.rmdir()
        except OSError:
            pass


async def runtime_schedule(database: Database, settings: Settings) -> ScheduleConfig:
    start_raw = await database.get_setting("window_start")
    end_raw = await database.get_setting("window_end")
    count_raw = await database.get_setting("posts_per_window")
    return ScheduleConfig(
        start=parse_clock(start_raw) if start_raw else settings.window_start,
        end=parse_clock(end_raw) if end_raw else settings.window_end,
        count=int(count_raw) if count_raw else settings.posts_per_window,
        timezone=settings.timezone,
    )


async def resolve_target_chat_id(database: Database, settings: Settings) -> int | None:
    if settings.target_chat_id is not None:
        return settings.target_chat_id
    target = await database.get_setting("target_chat_id")
    return int(target) if target else None


async def album_worker(database: Database, bot: Bot) -> None:
    while True:
        try:
            cutoff = datetime.now(UTC) - timedelta(seconds=2)
            finalized = await database.finalize_pending_albums(cutoff)
            for item_id, source_chat_id, media_count in finalized:
                queue_size = await database.queue_count()
                await bot.send_message(
                    source_chat_id,
                    f"Альбом добавлен в очередь: #{item_id} ({media_count} файлов). "
                    f"Сейчас в очереди: {queue_size}.",
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to finalize pending media albums")
        await asyncio.sleep(1)


async def scheduler_worker(
    database: Database,
    settings: Settings,
    publisher: Publisher,
    *,
    now_factory: Callable[[], datetime] | None = None,
) -> None:
    now_factory = now_factory or (lambda: datetime.now(UTC))
    while True:
        try:
            if not settings.posting_enabled:
                await asyncio.sleep(15)
                continue
            if await database.get_setting("paused") != "false":
                await asyncio.sleep(15)
                continue
            target_chat_id = await resolve_target_chat_id(database, settings)
            if target_chat_id is None or await database.queue_count() == 0:
                await asyncio.sleep(15)
                continue

            schedule = await runtime_schedule(database, settings)
            slot = active_slot(now_factory(), schedule)
            if slot is None or not await database.claim_slot(slot.key, slot.starts_at):
                await asyncio.sleep(15)
                continue

            item = await database.claim_next_item()
            if item is None:
                await database.finish_slot(slot.key, "failed", None, "Queue became empty")
                await asyncio.sleep(15)
                continue

            try:
                await publisher.publish(target_chat_id, item)
            except Exception as exc:
                logger.exception("Publication failed for queue item %s", item.id)
                await database.mark_publish_failed(item.id, str(exc))
                await database.finish_slot(slot.key, "failed", item.id, str(exc))
            else:
                await database.mark_published(item.id)
                await database.finish_slot(slot.key, "published", item.id)
                cleanup_local_media(item)
                logger.info("Published queue item %s to chat %s", item.id, target_chat_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unexpected scheduler failure")
        await asyncio.sleep(15)
