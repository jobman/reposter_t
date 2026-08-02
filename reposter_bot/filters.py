from aiogram import Bot
from aiogram.filters import BaseFilter


class BotIdFilter(BaseFilter):
    def __init__(self, bot_id: int) -> None:
        self.bot_id = bot_id

    async def __call__(self, event: object, bot: Bot) -> bool:
        del event
        return bot.id == self.bot_id
