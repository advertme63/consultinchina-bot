from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from config import config
from keyboards import menu_for

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Здравствуйте! Я бот Consult in China.\n"
        "Отвечу на вопросы о компании в Китае, налогах, оплате поставщикам и ликвидации — "
        "по нашим справочникам.\n"
        "Если хотите понять, выгодна ли вашему магазину китайская компания, нажмите «📊 Подходит ли мне».\n"
        "Невыгодно — так и скажем.\n\n"
        f"Для гостей — до {config.DAILY_LIMIT_GUEST} вопросов в сутки, для клиентов — до "
        f"{config.DAILY_LIMIT_CLIENT}. Мы сохраняем вопросы и ответы, чтобы бот отвечал точнее.",
        reply_markup=menu_for(message.from_user.id),
    )
