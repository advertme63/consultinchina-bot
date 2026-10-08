import json
import re

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import database
from config import config
from handlers.qualify import ask_segment
from keyboards import MAIN_MENU

router = Router()
router.message.filter(F.chat.type == "private")

# Метки deep-link: reels, tg, site, max, article_<id>; принимаем любые латиница/цифры/_/-, чтобы новый канал не требовал правки кода
SOURCE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, command: CommandObject) -> None:
    await state.clear()
    source = (command.args or "").strip()
    if source and SOURCE_RE.match(source):
        saved = await database.set_source_if_empty(message.from_user.id, source.lower())
    else:
        source, saved = "", False
    await database.log_event(message.from_user.id, "start", json.dumps({"source": source or None, "first": saved}))
    await message.answer(
        "Здравствуйте! Я бот Consult in China.\n"
        "Отвечу на вопросы о компании в Китае, налогах, оплате поставщикам и ликвидации — "
        "по нашим справочникам.\n"
        "Если хотите понять, выгодна ли вашему магазину китайская компания, нажмите «📊 Подходит ли мне».\n"
        "Невыгодно — так и скажем.\n\n"
        f"Для гостей — до {config.DAILY_LIMIT_GUEST} вопросов в сутки, для клиентов — до "
        f"{config.DAILY_LIMIT_CLIENT}. Мы сохраняем вопросы и ответы, чтобы бот отвечал точнее.",
        reply_markup=MAIN_MENU,
    )
    user = await database.get_user(message.from_user.id)
    if user and not user["segment"]:
        await ask_segment(message, state, "start")
