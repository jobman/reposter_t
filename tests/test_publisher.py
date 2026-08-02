from unittest.mock import AsyncMock

import pytest
from aiogram.enums import ParseMode

from reposter_bot.database import MediaRecord, QueueItem
from reposter_bot.publisher import Publisher


@pytest.mark.asyncio
async def test_publisher_uses_configured_link_text() -> None:
    bot = AsyncMock()
    publisher = Publisher(bot, "https://t.me/+invite", "Toy 🖤")
    item = QueueItem(
        id=1,
        media=(MediaRecord(kind="photo", file_id="photo-id"),),
        attempts=0,
    )

    await publisher.publish(-100123, item)

    bot.send_photo.assert_awaited_once_with(
        photo="photo-id",
        chat_id=-100123,
        caption='<a href="https://t.me/+invite">Toy 🖤</a>',
        parse_mode=ParseMode.HTML,
    )
