from aiogram import Bot
from aiogram.filters import BaseFilter


class BotIdFilter(BaseFilter):
    def __init__(self, bot_id: int) -> None:
        self.bot_id = bot_id

    async def __call__(self, bot: Bot) -> bool:
        return bot.id == self.bot_id
