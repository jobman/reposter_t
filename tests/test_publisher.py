from unittest.mock import AsyncMock

import pytest
from aiogram.enums import ParseMode

from reposter_bot.database import MediaRecord, QueueItem
from reposter_bot.publisher import Publisher


@pytest.mark.asyncio
async def test_publisher_uses_configured_link_text() -> None:
    bot = AsyncMock()
    publisher = Publisher(
        bot,
        "https://t.me/+invite",
        "Toy 🖤",
        "https://t.me/toy_predlozhka_bot",
        "Предложка",
    )
    item = QueueItem(
        id=1,
        media=(MediaRecord(kind="photo", file_id="photo-id"),),
        attempts=0,
    )

    await publisher.publish(-100123, item)

    bot.send_photo.assert_awaited_once_with(
        photo="photo-id",
        chat_id=-100123,
        caption=(
            '<a href="https://t.me/+invite">Toy 🖤</a> | '
            '<a href="https://t.me/toy_predlozhka_bot">Предложка</a>'
        ),
        parse_mode=ParseMode.HTML,
    )


@pytest.mark.asyncio
async def test_suggestion_adds_hashtag() -> None:
    bot = AsyncMock()
    publisher = Publisher(
        bot,
        "https://t.me/+invite",
        "Toy 🖤",
        "https://t.me/toy_predlozhka_bot",
        "Предложка",
    )
    item = QueueItem(
        id=2,
        media=(MediaRecord(kind="video", file_id="video-id"),),
        attempts=0,
        is_suggestion=True,
    )

    await publisher.publish(-100123, item)

    bot.send_video.assert_awaited_once()
    assert bot.send_video.await_args.kwargs["caption"].endswith("\n#предложка")


@pytest.mark.asyncio
async def test_text_suggestion_is_escaped_and_published_with_footer() -> None:
    bot = AsyncMock()
    publisher = Publisher(
        bot,
        "https://t.me/+invite",
        "Toy 🖤",
        "https://t.me/toy_predlozhka_bot",
        "Предложка",
    )
    item = QueueItem(
        id=3,
        media=(),
        attempts=0,
        is_suggestion=True,
        text_content="Текст <автора>",
    )

    await publisher.publish(-100123, item)

    bot.send_message.assert_awaited_once()
    text = bot.send_message.await_args.kwargs["text"]
    assert text.startswith("Текст &lt;автора&gt;")
    assert text.endswith("#предложка")
