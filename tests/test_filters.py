import pytest
from aiogram import Bot

from reposter_bot.filters import BotIdFilter


@pytest.mark.asyncio
async def test_bot_id_filter_accepts_event_and_injected_bot() -> None:
    first_bot = Bot("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi")
    second_bot = Bot("987654321:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi")
    try:
        event = object()
        assert await BotIdFilter(first_bot.id)(event, bot=first_bot) is True
        assert await BotIdFilter(first_bot.id)(event, bot=second_bot) is False
    finally:
        await first_bot.session.close()
        await second_bot.session.close()
