"""Любой текст вне сценариев = вопрос (ТЗ 4.1). Меню, лимиты (ТЗ 2), кнопки под ответом (ТЗ 4.2)."""
import json
import logging
from typing import Optional

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.utils.chat_action import ChatActionSender

import database
import keyboards as kb
from services.answer import answer_question
from services.limits import daily_limit, plural_questions, shanghai_today, until_reset
from services.tg_format import to_plain, to_telegram_html

logger = logging.getLogger(__name__)
router = Router()

COMING_SOON = "Этот раздел скоро заработает. А пока напишите вопрос своими словами — отвечу по нашим справочникам."
LIMIT_EXHAUSTED = (
    "Лимит на сегодня исчерпан, он обновится в 00:00 по Шанхаю. "
    "Если вопрос срочный — оставьте контакт, Валерий Загурский ответит лично."
)


@router.message(F.text.in_({kb.BTN_ASK, kb.LEGACY_KB}))
async def ask_hint(message: Message) -> None:
    await message.answer("Напишите вопрос своими словами.", reply_markup=kb.MAIN_MENU)


@router.message(F.text.in_({kb.BTN_QUALIFY, kb.BTN_MANAGER, kb.LEGACY_TICKETS}))
async def coming_soon(message: Message) -> None:
    await message.answer(COMING_SOON, reply_markup=kb.MAIN_MENU)


@router.callback_query(F.data.in_({"cta:qualify", "cta:manager"}))
async def cta_coming_soon(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(COMING_SOON)


@router.message(F.text == kb.BTN_LIMIT)
@router.message(Command("limit"))
async def my_limit(message: Message, user: Optional[dict] = None) -> None:
    limit = daily_limit(message.from_user.id, user["role"] if user else None)
    if limit is None:
        await message.answer("У вас нет лимита на вопросы.")
        return
    used = await database.questions_used(message.from_user.id, shanghai_today())
    left = until_reset()
    hours, minutes = left.seconds // 3600, left.seconds % 3600 // 60
    await message.answer(
        f"Сегодня использовано {used} из {limit}.\n"
        f"Лимит обновится в 00:00 по Шанхаю — через {hours} ч {minutes} мин."
    )


async def _send(message: Message, text: str, footer: str = "", reply_markup=None) -> None:
    """HTML с экранированием; если Telegram отклонил разметку — повтор без неё (ТЗ 4.4)."""
    try:
        html = to_telegram_html(text) + (f"\n\n<i>{footer}</i>" if footer else "")
        await message.answer(html, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        logger.warning("Telegram отклонил HTML (%s), отправляю без разметки", e)
        plain = to_plain(text) + (f"\n\n{footer}" if footer else "")
        await message.answer(plain, reply_markup=reply_markup, parse_mode=None)


@router.message(F.text, ~F.text.startswith("/"))
async def handle_question(message: Message, user: Optional[dict] = None) -> None:
    uid = message.from_user.id
    limit = daily_limit(uid, user["role"] if user else None)
    today = shanghai_today()
    used = None
    if limit is not None:
        used = await database.take_question(uid, today, limit)
        if used is None:
            await database.log_event(uid, "limit_hit", json.dumps({"limit": limit}))
            await message.answer(LIMIT_EXHAUSTED, reply_markup=kb.manager_keyboard())
            return

    try:
        async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
            result = await answer_question(uid, message.text)
    except Exception:
        logger.exception("answer_question failed for user %s", uid)
        if limit is not None:
            await database.refund_question(uid, today)
        await message.answer("Не получилось ответить — сервис временно недоступен. Попробуйте ещё раз через минуту.")
        return

    footer = ""
    if limit is not None and 0 < limit - used <= 3:
        footer = f"Осталось {plural_questions(limit - used)} на сегодня"
    await _send(message, result.answer, footer, kb.cta_keyboard(result.intent, result.cta))
