from __future__ import annotations

import hmac
import logging
from datetime import UTC, datetime
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from reposter_bot.config import Settings, parse_clock
from reposter_bot.database import Database
from reposter_bot.filters import BotIdFilter
from reposter_bot.scheduling import next_slot
from reposter_bot.workers import resolve_target_chat_id, runtime_schedule

logger = logging.getLogger(__name__)


def build_router(database: Database, settings: Settings, bot_id: int) -> Router:
    router = Router(name="reposter")
    router.message.filter(BotIdFilter(bot_id))
    router.channel_post.filter(BotIdFilter(bot_id))

    async def is_admin(message: Message) -> bool:
        if message.from_user is None:
            return False
        user_id = message.from_user.id
        return user_id in settings.admin_user_ids or await database.is_owner(user_id)

    async def require_admin(message: Message) -> bool:
        if await is_admin(message):
            return True
        await message.answer("Нет доступа. Владелец может авторизоваться командой /claim SECRET.")
        return False

    @router.channel_post()
    async def discover_channel(message: Message) -> None:
        await database.set_setting("last_discovered_channel_id", str(message.chat.id))
        await database.set_setting("last_discovered_channel_title", message.chat.title or "")
        logger.info("Discovered channel %s (%s)", message.chat.id, message.chat.title or "untitled")

    @router.message(Command("start"), F.chat.type == "private")
    async def start(message: Message) -> None:
        if await is_admin(message):
            await message.answer(
                "Бот готов принимать фото, видео, GIF и альбомы. Подписи исходных сообщений "
                "не сохраняются. Команды: /status, /queue, /schedule, /pause, /resume."
            )
            return
        await message.answer(
            "Сначала авторизуйтесь: /claim SECRET\nSECRET хранится в конфигурации сервера."
        )

    @router.message(Command("claim"), F.chat.type == "private")
    async def claim(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        if await is_admin(message):
            await message.answer("Вы уже авторизованы.")
            return
        supplied = (command.args or "").strip()
        if not settings.setup_secret:
            await message.answer("SETUP_SECRET не настроен на сервере.")
            return
        if not hmac.compare_digest(supplied, settings.setup_secret):
            await message.answer("Неверный секрет.")
            return
        if await database.claim_owner(message.from_user.id):
            await message.answer("Готово: вы назначены владельцем бота. Используйте /status.")
        else:
            await message.answer("Владелец уже назначен.")

    @router.message(Command("status"), F.chat.type == "private")
    async def status(message: Message) -> None:
        if not await require_admin(message):
            return
        schedule = await runtime_schedule(database, settings)
        target_chat_id = await resolve_target_chat_id(database, settings)
        discovered_id = await database.get_setting("last_discovered_channel_id")
        discovered_title = await database.get_setting("last_discovered_channel_title") or "—"
        paused = await database.get_setting("paused") != "false"
        queue_size = await database.queue_count()
        upcoming = next_slot(datetime.now(UTC), schedule)
        hard_lock = "включена" if settings.posting_enabled else "ВЫКЛЮЧЕНА (discovery)"
        await message.answer(
            "Статус бота\n"
            f"Публикация на сервере: {hard_lock}\n"
            f"Очередь: {queue_size}\n"
            f"Пауза: {'да' if paused else 'нет'}\n"
            f"Целевой chat_id: {target_chat_id or 'не задан'}\n"
            f"Последний обнаруженный канал: {escape(discovered_title)} "
            f"({discovered_id or 'не обнаружен'})\n"
            f"Расписание: {schedule.start.strftime('%H:%M')}–{schedule.end.strftime('%H:%M')}, "
            f"{schedule.count} постов, {settings.timezone.key}\n"
            f"Следующий слот: {upcoming.strftime('%Y-%m-%d %H:%M:%S %Z')}"
        )

    @router.message(Command("usechannel"), F.chat.type == "private")
    async def use_channel(message: Message) -> None:
        if not await require_admin(message):
            return
        discovered_id = await database.get_setting("last_discovered_channel_id")
        if not discovered_id:
            await message.answer(
                "Канал ещё не обнаружен. Добавьте бота в канал и опубликуйте один пост."
            )
            return
        await database.set_setting("target_chat_id", discovered_id)
        await message.answer(
            f"Целевой chat_id сохранён: {discovered_id}. Публикация остаётся на паузе."
        )

    @router.message(Command("schedule"), F.chat.type == "private")
    async def change_schedule(message: Message, command: CommandObject) -> None:
        if not await require_admin(message):
            return
        parts = (command.args or "").split()
        if len(parts) != 3:
            await message.answer("Формат: /schedule 19:00 02:00 10")
            return
        try:
            start, end = parse_clock(parts[0]), parse_clock(parts[1])
            count = int(parts[2])
            if not 1 <= count <= 100:
                raise ValueError
        except ValueError:
            await message.answer("Проверьте время HH:MM и количество от 1 до 100.")
            return
        await database.set_setting("window_start", start.strftime("%H:%M"))
        await database.set_setting("window_end", end.strftime("%H:%M"))
        await database.set_setting("posts_per_window", str(count))
        await message.answer(
            f"Расписание сохранено: {start.strftime('%H:%M')}–{end.strftime('%H:%M')}, "
            f"{count} постов равномерно."
        )

    @router.message(Command("pause"), F.chat.type == "private")
    async def pause(message: Message) -> None:
        if not await require_admin(message):
            return
        await database.set_setting("paused", "true")
        await message.answer("Публикация поставлена на паузу.")

    @router.message(Command("resume"), F.chat.type == "private")
    async def resume(message: Message) -> None:
        if not await require_admin(message):
            return
        if not settings.posting_enabled:
            await message.answer(
                "Публикация заблокирована серверным флагом POSTING_ENABLED=false. "
                "Сначала включите рабочий режим на сервере."
            )
            return
        if await resolve_target_chat_id(database, settings) is None:
            await message.answer("Сначала определите канал и выполните /usechannel.")
            return
        await database.set_setting("paused", "false")
        await message.answer("Публикация снята с паузы.")

    @router.message(Command("queue"), F.chat.type == "private")
    async def show_queue(message: Message) -> None:
        if not await require_admin(message):
            return
        items = await database.list_queue()
        if not items:
            await message.answer("Очередь пуста.")
            return
        lines = ["Первые элементы очереди:"]
        for item in items:
            lines.append(
                f"#{item['id']} — {item['kinds']}, файлов: {item['media_count']}, "
                f"попыток: {item['attempts']}"
            )
        await message.answer("\n".join(lines))

    @router.message(Command("delete"), F.chat.type == "private")
    async def delete_item(message: Message, command: CommandObject) -> None:
        if not await require_admin(message):
            return
        try:
            item_id = int((command.args or "").strip())
        except ValueError:
            await message.answer("Формат: /delete ID")
            return
        removed = await database.remove_queued(item_id)
        await message.answer("Удалено из очереди." if removed else "Такого элемента в очереди нет.")

    @router.message(F.chat.type == "private")
    async def ingest(message: Message, bot: Bot) -> None:
        if not await require_admin(message):
            return
        media: tuple[str, str] | None = None
        if message.photo:
            media = ("photo", message.photo[-1].file_id)
        elif message.video:
            media = ("video", message.video.file_id)
        elif message.animation:
            media = ("animation", message.animation.file_id)
        elif message.document:
            media = ("document", message.document.file_id)
        elif message.audio:
            media = ("audio", message.audio.file_id)
        if media is None:
            await message.answer(
                "Поддерживаются фото, видео, GIF, документы, аудио и медиаальбомы."
            )
            return

        kind, file_id = media
        if message.media_group_id:
            if message.from_user is None:
                return
            await database.add_album_part(
                source_chat_id=message.chat.id,
                source_message_id=message.message_id,
                media_group_id=message.media_group_id,
                sender_user_id=message.from_user.id,
                kind=kind,
                file_id=file_id,
            )
            return

        item_id, created = await database.add_single(
            source_chat_id=message.chat.id,
            source_message_id=message.message_id,
            kind=kind,
            file_id=file_id,
        )
        if created:
            queue_size = await database.queue_count()
            await bot.send_message(
                message.chat.id,
                f"Добавлено в очередь: #{item_id}. Сейчас в очереди: {queue_size}.",
            )

    return router
