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
from states import QualifyStates

router = Router()
router.message.filter(F.chat.type == "private")

# Метки deep-link: reels, tg, site, max, article_<id>; принимаем любые латиница/цифры/_/-, чтобы новый канал не требовал правки кода
SOURCE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def greeting_e6() -> str:
    """Приветствие Э6 (справочник по подписке) — текст ТЗ дословно."""
    return (
        "Здравствуйте! Это справочник-консультант Consult in China — помогаем разобраться в бизнесе с Китаем.\n\n"
        "<b>Что умеет бот:</b>\n"
        "📚 <b>Справочник</b> — ответы о компании в Китае (WFOE, Гонконг, представительство), налогах и бухгалтерии, "
        "оплате поставщикам и фапьяо, поиске и проверке поставщиков, логистике, найме и визах, закрытии компании.\n"
        "📊 <b>Подходит ли мне компания в Китае</b> — за пару минут оценим, есть ли смысл открывать её именно вам. "
        "Невыгодно — так и скажем.\n"
        "👤 <b>Связаться со специалистом</b> — передадим ваш вопрос нашему специалисту.\n"
        "📄 <b>Материалы</b> — презентации о наших услугах.\n\n"
        "Ответы — по справочникам нашей команды: 10+ лет в Китае, 350+ клиентов, офис в Шанхае.\n\n"
        f"Справочник бесплатный. Нужна только подписка на наш канал {config.SUB_CHANNEL} — там разборы и новости "
        f"о бизнесе с Китаем. С подпиской — до {config.DAILY_LIMIT_GUEST} вопросов в день. "
        "Мы сохраняем вопросы и ответы, чтобы бот отвечал точнее."
    )


def greeting_old() -> str:
    """До Э6 (SUB_GATE_ENABLED=0 — всё как раньше)."""
    return (
        "Здравствуйте! Я бот Consult in China.\n"
        "Отвечу на вопросы о компании в Китае, налогах, оплате поставщикам и ликвидации — "
        "по нашим справочникам.\n"
        "Если хотите понять, выгодна ли вашему магазину китайская компания, нажмите «📊 Подходит ли мне».\n"
        "Невыгодно — так и скажем.\n\n"
        f"{limits_line()} Мы сохраняем вопросы и ответы, чтобы бот отвечал точнее."
    )


def limits_line() -> str:
    if config.DAILY_LIMIT_GUEST == config.DAILY_LIMIT_CLIENT:
        return f"До {config.DAILY_LIMIT_GUEST} вопросов в сутки."
    return f"Для гостей — до {config.DAILY_LIMIT_GUEST} вопросов в сутки, для клиентов — до {config.DAILY_LIMIT_CLIENT}."


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, command: CommandObject) -> None:
    was_in_questionnaire = (await state.get_state() or "").startswith(QualifyStates.__name__)
    await state.clear()
    source = (command.args or "").strip()
    if source and SOURCE_RE.match(source):
        saved = await database.set_source_if_empty(message.from_user.id, source.lower())
    else:
        source, saved = "", False
    await database.log_event(message.from_user.id, "start", json.dumps({"source": source or None, "first": saved}))
    await message.answer(greeting_e6() if config.SUB_GATE_ENABLED else greeting_old(), reply_markup=MAIN_MENU)
    user = await database.get_user(message.from_user.id)
    if was_in_questionnaire:  # /start посреди анкеты: анкета сброшена — снова «Что вас привело?»
        await ask_segment(message, state, "q")
    elif user and not user["segment"]:
        await ask_segment(message, state, "start")
