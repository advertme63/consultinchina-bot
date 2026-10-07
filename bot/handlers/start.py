from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from keyboards import MAIN_MENU

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Здравствуйте! Я — умный справочник ConsultInChina.\n\n"
        "📚 <b>Справочник</b> — отвечу на вопросы по регистрации, бухгалтерии, ликвидации "
        "и другим темам на основе наших материалов\n"
        "✉️ <b>Обращения</b> — если не нашли ответ, оставьте вопрос, и мы свяжемся с вами\n"
        "📄 <b>Документы</b> — полезные материалы для скачивания",
        reply_markup=MAIN_MENU,
    )
