from __future__ import annotations

import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from reposter_bot.config import Settings
from reposter_bot.database import Database
from reposter_bot.handlers import build_router
from reposter_bot.publisher import Publisher
from reposter_bot.suggestions import build_suggestion_router, suggestion_review_worker
from reposter_bot.workers import album_worker, scheduler_worker


async def main() -> None:
    settings = Settings.from_env()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger(__name__)

    database = Database(settings.database_path)
    await database.open()
    bot_defaults = DefaultBotProperties(parse_mode=ParseMode.HTML)
    bot = Bot(settings.bot_token, default=bot_defaults)
    suggestion_bot = (
        Bot(settings.suggestion_bot_token, default=bot_defaults)
        if settings.suggestion_bot_token
        else None
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(database, settings, bot.id))
    if suggestion_bot is not None:
        dispatcher.include_router(build_suggestion_router(database, settings, suggestion_bot.id))
    publisher = Publisher(
        bot,
        settings.join_url,
        settings.link_text,
        settings.suggestion_bot_url,
        settings.suggestion_link_text,
    )

    await bot.delete_webhook(drop_pending_updates=False)
    await bot.set_my_commands(
        [
            BotCommand(command="status", description="статус и настройки"),
            BotCommand(command="queue", description="показать очередь"),
            BotCommand(command="schedule", description="изменить расписание"),
            BotCommand(command="pause", description="поставить на паузу"),
            BotCommand(command="resume", description="снять с паузы"),
            BotCommand(command="usechannel", description="выбрать обнаруженный канал"),
            BotCommand(command="delete", description="удалить элемент очереди"),
        ]
    )
    identity = await bot.get_me()
    logger.info(
        "Starting @%s; posting_enabled=%s; target_chat_id=%s",
        identity.username,
        settings.posting_enabled,
        settings.target_chat_id,
    )

    polling_bots = [bot]
    if suggestion_bot is not None:
        await suggestion_bot.delete_webhook(drop_pending_updates=False)
        await suggestion_bot.set_my_commands(
            [BotCommand(command="start", description="как отправить предложение")]
        )
        suggestion_identity = await suggestion_bot.get_me()
        polling_bots.append(suggestion_bot)
        logger.info(
            "Starting suggestion bot @%s; admin_id=%s",
            suggestion_identity.username,
            settings.suggestion_admin_id,
        )

    workers = [
        asyncio.create_task(album_worker(database, bot), name="album-worker"),
        asyncio.create_task(
            scheduler_worker(database, settings, publisher), name="scheduler-worker"
        ),
    ]
    if suggestion_bot is not None:
        workers.append(
            asyncio.create_task(
                suggestion_review_worker(database, settings, suggestion_bot),
                name="suggestion-review-worker",
            )
        )
    try:
        await dispatcher.start_polling(
            *polling_bots,
            allowed_updates=dispatcher.resolve_used_update_types(),
            close_bot_session=False,
        )
    finally:
        for task in workers:
            task.cancel()
        for task in workers:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await bot.session.close()
        if suggestion_bot is not None:
            await suggestion_bot.session.close()
        await database.close()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
