import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

import database
from config import config
from handlers import admin, documents, questions, start
from middlewares.access import AccessMiddleware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    await database.init_pool()

    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.middleware(AccessMiddleware())

    dp.include_router(admin.router)
    dp.include_router(start.router)
    dp.include_router(documents.router)
    dp.include_router(questions.router)  # последним: любой текст = вопрос

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Bot starting (polling mode)")
        await dp.start_polling(bot)
    finally:
        await database.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
