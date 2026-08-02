from __future__ import annotations

from html import escape
from pathlib import Path

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import (
    FSInputFile,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
)

from reposter_bot.database import MediaRecord, QueueItem


class Publisher:
    def __init__(
        self,
        bot: Bot,
        join_url: str,
        link_text: str,
        suggestion_bot_url: str,
        suggestion_link_text: str,
    ) -> None:
        self.bot = bot
        self.base_footer = (
            f'<a href="{escape(join_url, quote=True)}">{escape(link_text)}</a> | '
            f'<a href="{escape(suggestion_bot_url, quote=True)}">'
            f"{escape(suggestion_link_text)}</a>"
        )

    def footer(self, *, is_suggestion: bool) -> str:
        if is_suggestion:
            return f"{self.base_footer}\n#предложка"
        return self.base_footer

    async def publish(self, target_chat_id: int, item: QueueItem) -> None:
        caption = self.footer(is_suggestion=item.is_suggestion)
        if not item.media:
            if not item.text_content:
                raise ValueError(f"Queue item {item.id} has no content")
            await self.bot.send_message(
                chat_id=target_chat_id,
                text=f"{escape(item.text_content)}\n\n{caption}",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            return
        if len(item.media) == 1:
            await self._send_single(target_chat_id, item.media[0], caption)
            return

        media_group = []
        for index, media in enumerate(item.media):
            item_caption = caption if index == 0 else None
            parse_mode = ParseMode.HTML if index == 0 else None
            media_value = self._media_value(media)
            if media.kind == "photo":
                media_group.append(
                    InputMediaPhoto(media=media_value, caption=item_caption, parse_mode=parse_mode)
                )
            elif media.kind == "video":
                media_group.append(
                    InputMediaVideo(media=media_value, caption=item_caption, parse_mode=parse_mode)
                )
            elif media.kind == "document":
                media_group.append(
                    InputMediaDocument(
                        media=media_value, caption=item_caption, parse_mode=parse_mode
                    )
                )
            elif media.kind == "audio":
                media_group.append(
                    InputMediaAudio(media=media_value, caption=item_caption, parse_mode=parse_mode)
                )
            else:
                raise ValueError(f"Unsupported media kind in album: {media.kind}")
        await self.bot.send_media_group(chat_id=target_chat_id, media=media_group)

    @staticmethod
    def _media_value(media: MediaRecord) -> str | FSInputFile:
        if media.local_path:
            return FSInputFile(Path(media.local_path))
        return media.file_id

    async def _send_single(self, target_chat_id: int, media: MediaRecord, caption: str) -> None:
        common = {
            "chat_id": target_chat_id,
            "caption": caption,
            "parse_mode": ParseMode.HTML,
        }
        media_value = self._media_value(media)
        if media.kind == "photo":
            await self.bot.send_photo(photo=media_value, **common)
        elif media.kind == "video":
            await self.bot.send_video(video=media_value, **common)
        elif media.kind == "animation":
            await self.bot.send_animation(animation=media_value, **common)
        elif media.kind == "document":
            await self.bot.send_document(document=media_value, **common)
        elif media.kind == "audio":
            await self.bot.send_audio(audio=media_value, **common)
        else:
            raise ValueError(f"Unsupported media kind: {media.kind}")
