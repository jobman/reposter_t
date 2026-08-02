from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from reposter_bot.config import Settings
from reposter_bot.database import Database, MediaRecord, Suggestion
from reposter_bot.filters import BotIdFilter

logger = logging.getLogger(__name__)


def cleanup_downloads(downloaded: list[MediaRecord], target_dir: Path) -> None:
    for media in downloaded:
        if media.local_path:
            Path(media.local_path).unlink(missing_ok=True)
    try:
        target_dir.rmdir()
    except OSError:
        pass


def review_keyboard(suggestion_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Approve", callback_data=f"suggest:approve:{suggestion_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Decline", callback_data=f"suggest:decline:{suggestion_id}"
                ),
            ]
        ]
    )


def extract_media(message: Message) -> MediaRecord | None:
    if message.photo:
        return MediaRecord(kind="photo", file_id=message.photo[-1].file_id)
    if message.video:
        return MediaRecord(kind="video", file_id=message.video.file_id)
    if message.animation:
        return MediaRecord(kind="animation", file_id=message.animation.file_id)
    if message.document:
        return MediaRecord(kind="document", file_id=message.document.file_id)
    if message.audio:
        return MediaRecord(kind="audio", file_id=message.audio.file_id)
    return None


def review_text(suggestion: Suggestion) -> str:
    author = escape(suggestion.submitter_name)
    username = (
        f" (@{escape(suggestion.submitter_username)})" if suggestion.submitter_username else ""
    )
    return (
        f"Предложение #{suggestion.id}\n"
        f'<a href="tg://user?id={suggestion.submitter_user_id}">{author}</a>{username}\n'
        f"User ID: <code>{suggestion.submitter_user_id}</code>"
    )


async def send_suggestion_for_review(
    database: Database, settings: Settings, bot: Bot, suggestion_id: int
) -> None:
    suggestion = await database.get_suggestion(suggestion_id)
    if suggestion is None or suggestion.status != "pending":
        return
    await bot.copy_messages(
        chat_id=settings.suggestion_admin_id,
        from_chat_id=suggestion.submitter_chat_id,
        message_ids=list(suggestion.source_message_ids),
    )
    control = await bot.send_message(
        chat_id=settings.suggestion_admin_id,
        text=review_text(suggestion),
        reply_markup=review_keyboard(suggestion.id),
    )
    await database.set_suggestion_review_message(suggestion.id, control.message_id)


async def suggestion_review_worker(database: Database, settings: Settings, bot: Bot) -> None:
    while True:
        try:
            cutoff = datetime.now(UTC) - timedelta(seconds=2)
            await database.finalize_suggestion_albums(cutoff)
            for suggestion_id in await database.pending_unreviewed_suggestions():
                try:
                    await send_suggestion_for_review(database, settings, bot, suggestion_id)
                except Exception:
                    logger.exception("Failed to send suggestion %s for review", suggestion_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Suggestion review worker failed")
        await asyncio.sleep(1)


async def download_suggestion_media(
    bot: Bot, suggestion: Suggestion, media_root: Path
) -> tuple[MediaRecord, ...]:
    if not suggestion.media:
        return ()
    target_dir = media_root / str(suggestion.id)
    target_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[MediaRecord] = []
    try:
        for position, media in enumerate(suggestion.media):
            telegram_file = await bot.get_file(media.file_id)
            if not telegram_file.file_path:
                raise ValueError(f"Telegram did not return a path for media {position}")
            suffix = Path(telegram_file.file_path).suffix[:12] or ".bin"
            destination = target_dir / f"{position:02d}{suffix}"
            await bot.download_file(telegram_file.file_path, destination=destination)
            downloaded.append(
                MediaRecord(
                    kind=media.kind,
                    file_id="",
                    local_path=str(destination.resolve()),
                )
            )
    except Exception:
        await asyncio.to_thread(cleanup_downloads, downloaded, target_dir)
        raise
    return tuple(downloaded)


def build_suggestion_router(database: Database, settings: Settings, bot_id: int) -> Router:
    router = Router(name="suggestions")
    router.message.filter(BotIdFilter(bot_id))
    router.callback_query.filter(BotIdFilter(bot_id))

    @router.message(Command("start"), F.chat.type == "private")
    async def start(message: Message) -> None:
        if message.from_user and message.from_user.id == settings.suggestion_admin_id:
            await message.answer(
                "Вы администратор предложки. Новые материалы появятся здесь с кнопками "
                "✅ Approve и ❌ Decline."
            )
            return
        await message.answer(
            "Отправьте сюда текст, фото, видео, GIF или альбом. "
            "После проверки администратором материал может попасть в канал."
        )

    @router.callback_query(
        F.from_user.id == settings.suggestion_admin_id,
        F.data.startswith("suggest:approve:"),
    )
    async def approve(callback: CallbackQuery, bot: Bot) -> None:
        suggestion_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
        if not await database.begin_suggestion_approval(suggestion_id):
            await callback.answer("Это предложение уже обработано.", show_alert=True)
            return
        await callback.answer("Добавляю в очередь…")
        suggestion = await database.get_suggestion(suggestion_id)
        if suggestion is None:
            return
        if suggestion.review_message_id:
            await bot.edit_message_reply_markup(
                chat_id=settings.suggestion_admin_id,
                message_id=suggestion.review_message_id,
                reply_markup=None,
            )
        try:
            local_media = await download_suggestion_media(
                bot, suggestion, settings.suggestion_media_path
            )
            queue_item_id = await database.enqueue_approved_suggestion(suggestion.id, local_media)
        except Exception as exc:
            logger.exception("Failed to approve suggestion %s", suggestion.id)
            await database.reset_suggestion_pending(suggestion.id)
            if suggestion.review_message_id:
                await bot.edit_message_reply_markup(
                    chat_id=settings.suggestion_admin_id,
                    message_id=suggestion.review_message_id,
                    reply_markup=review_keyboard(suggestion.id),
                )
            await bot.send_message(
                settings.suggestion_admin_id,
                f"Не удалось добавить предложение #{suggestion.id}: {escape(str(exc))}",
            )
            return

        if suggestion.review_message_id:
            await bot.edit_message_text(
                chat_id=settings.suggestion_admin_id,
                message_id=suggestion.review_message_id,
                text=f"✅ Approved #{suggestion.id}\nДобавлено в очередь: #{queue_item_id}",
            )
        try:
            await bot.send_message(
                suggestion.submitter_chat_id,
                f"✅ Ваше предложение #{suggestion.id} одобрено и добавлено в очередь.",
            )
        except (TelegramBadRequest, TelegramForbiddenError):
            logger.info("Submitter blocked the bot for suggestion %s", suggestion.id)

    @router.callback_query(
        F.from_user.id == settings.suggestion_admin_id,
        F.data.startswith("suggest:decline:"),
    )
    async def decline(callback: CallbackQuery, bot: Bot) -> None:
        suggestion_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
        if not await database.begin_suggestion_decline(suggestion_id):
            await callback.answer("Это предложение уже обработано.", show_alert=True)
            return
        suggestion = await database.get_suggestion(suggestion_id)
        if suggestion is None:
            return
        await callback.answer("Укажите причину отказа")
        if suggestion.review_message_id:
            await bot.edit_message_reply_markup(
                chat_id=settings.suggestion_admin_id,
                message_id=suggestion.review_message_id,
                reply_markup=None,
            )
        prompt = await bot.send_message(
            settings.suggestion_admin_id,
            f"Почему отклонено предложение #{suggestion.id}? Ответьте на это сообщение.",
            reply_markup=ForceReply(selective=True, input_field_placeholder="Причина отказа"),
        )
        await database.set_suggestion_reason_prompt(suggestion.id, prompt.message_id)

    @router.message(
        F.from_user.id == settings.suggestion_admin_id,
        F.reply_to_message,
        F.text,
    )
    async def decline_reason(message: Message, bot: Bot) -> None:
        if message.reply_to_message is None or message.text is None:
            return
        suggestion = await database.suggestion_awaiting_prompt(message.reply_to_message.message_id)
        if suggestion is None:
            await message.answer("Не найдено предложение, ожидающее эту причину.")
            return
        reason = message.text.strip()
        if not reason:
            await message.answer("Причина не может быть пустой.")
            return
        if not await database.mark_suggestion_declined(suggestion.id, reason):
            await message.answer("Предложение уже обработано.")
            return
        if suggestion.review_message_id:
            await bot.edit_message_text(
                chat_id=settings.suggestion_admin_id,
                message_id=suggestion.review_message_id,
                text=f"❌ Declined #{suggestion.id}\nПричина: {escape(reason)}",
            )
        await message.answer(f"Причина для #{suggestion.id} сохранена.")
        try:
            await bot.send_message(
                suggestion.submitter_chat_id,
                f"❌ Ваше предложение #{suggestion.id} отклонено.\nПричина: {escape(reason)}",
            )
        except (TelegramBadRequest, TelegramForbiddenError):
            logger.info("Submitter blocked the bot for suggestion %s", suggestion.id)

    @router.message(F.from_user.id == settings.suggestion_admin_id)
    async def admin_message(message: Message) -> None:
        await message.answer("Для модерации используйте кнопки под предложениями.")

    @router.message(F.chat.type == "private")
    async def submit(message: Message) -> None:
        if message.from_user is None or message.from_user.is_bot:
            return
        media = extract_media(message)
        source_text = message.caption or message.text
        if media is None and not source_text:
            await message.answer(
                "Поддерживаются текст, фото, видео, GIF, документы, аудио и альбомы."
            )
            return
        user = message.from_user
        if message.media_group_id and media is not None:
            suggestion_id, created = await database.add_suggestion_album_part(
                submitter_user_id=user.id,
                submitter_chat_id=message.chat.id,
                submitter_username=user.username,
                submitter_name=user.full_name,
                source_message_id=message.message_id,
                source_text=source_text,
                media_group_id=message.media_group_id,
                media=media,
            )
            if created:
                await message.answer(
                    f"Альбом принят как предложение #{suggestion_id} и отправляется на проверку."
                )
            return
        suggestion_id = await database.create_suggestion(
            submitter_user_id=user.id,
            submitter_chat_id=message.chat.id,
            submitter_username=user.username,
            submitter_name=user.full_name,
            source_message_id=message.message_id,
            source_text=source_text,
            media=media,
        )
        await message.answer(f"Предложение #{suggestion_id} принято и отправлено администратору.")

    return router
