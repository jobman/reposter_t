from __future__ import annotations

from html import escape

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import (
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
)

from reposter_bot.database import MediaRecord, QueueItem


class Publisher:
    def __init__(self, bot: Bot, join_url: str, link_text: str) -> None:
        self.bot = bot
        self.caption = f'<a href="{escape(join_url, quote=True)}">{escape(link_text)}</a>'

    async def publish(self, target_chat_id: int, item: QueueItem) -> None:
        if not item.media:
            raise ValueError(f"Queue item {item.id} has no media")
        if len(item.media) == 1:
            await self._send_single(target_chat_id, item.media[0])
            return

        media_group = []
        for index, media in enumerate(item.media):
            caption = self.caption if index == 0 else None
            parse_mode = ParseMode.HTML if index == 0 else None
            if media.kind == "photo":
                media_group.append(
                    InputMediaPhoto(media=media.file_id, caption=caption, parse_mode=parse_mode)
                )
            elif media.kind == "video":
                media_group.append(
                    InputMediaVideo(media=media.file_id, caption=caption, parse_mode=parse_mode)
                )
            elif media.kind == "document":
                media_group.append(
                    InputMediaDocument(media=media.file_id, caption=caption, parse_mode=parse_mode)
                )
            elif media.kind == "audio":
                media_group.append(
                    InputMediaAudio(media=media.file_id, caption=caption, parse_mode=parse_mode)
                )
            else:
                raise ValueError(f"Unsupported media kind in album: {media.kind}")
        await self.bot.send_media_group(chat_id=target_chat_id, media=media_group)

    async def _send_single(self, target_chat_id: int, media: MediaRecord) -> None:
        common = {
            "chat_id": target_chat_id,
            "caption": self.caption,
            "parse_mode": ParseMode.HTML,
        }
        if media.kind == "photo":
            await self.bot.send_photo(photo=media.file_id, **common)
        elif media.kind == "video":
            await self.bot.send_video(video=media.file_id, **common)
        elif media.kind == "animation":
            await self.bot.send_animation(animation=media.file_id, **common)
        elif media.kind == "document":
            await self.bot.send_document(document=media.file_id, **common)
        elif media.kind == "audio":
            await self.bot.send_audio(audio=media.file_id, **common)
        else:
            raise ValueError(f"Unsupported media kind: {media.kind}")
