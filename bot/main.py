import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

import database
from config import config
from handlers import admin, buttons, chat_member, documents, leads, qualify, questions, start
from middlewares.access import AccessMiddleware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def build_dispatcher() -> Dispatcher:
    """Роутеры и middleware бота. Его же использует e2e-симулятор (scripts/e2e/simulate.py)."""
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(AccessMiddleware())
    dp.include_router(admin.router)
    dp.include_router(start.router)
    dp.include_router(documents.router)
    dp.include_router(qualify.router)
    dp.include_router(leads.router)
    dp.include_router(buttons.router)  # до questions: комментарий к 👎 — не вопрос
    dp.include_router(chat_member.router)
    dp.include_router(questions.router)  # последним: любой текст = вопрос
    return dp


async def main() -> None:
    await database.init_pool()

    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher()

    scheduler_task = None
    if config.SCHEDULER_ENABLED:  # только рабочий процесс; тесты и e2e main() не вызывают
        from services.scheduler import run_scheduler

        scheduler_task = asyncio.create_task(run_scheduler(bot))

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Bot starting (polling mode)")
        await dp.start_polling(bot)
    finally:
        if scheduler_task:
            scheduler_task.cancel()
        await database.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
