"""Любой текст вне сценариев = вопрос (ТЗ 4.1). Меню, лимиты (ТЗ 2), кнопки под ответом (ТЗ 4.2).
Э6: справочник (кнопка, текст, подсказки) — только подписчикам канала, см. services/subgate.py."""
import json
import logging
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.chat_action import ChatActionSender

import database
import keyboards as kb
from config import config
from services import subgate
from services.answer import answer_question, clean_question
from services.limits import daily_limit, plural_questions, shanghai_today, until_reset
from services.tg_format import to_plain, to_telegram_html

logger = logging.getLogger(__name__)
router = Router()
router.message.filter(F.chat.type == "private")

LIMIT_EXHAUSTED = (
    "Лимит на сегодня исчерпан, он обновится в 00:00 по Шанхаю. "
    "Если вопрос срочный — оставьте контакт, наш специалист ответит лично."
)


ASK_HINT = "Напишите вопрос своими словами. Например: „Сколько стоит открыть компанию?“ или „Как получить фапьяо от поставщика?“"


@router.message(F.text.in_({kb.BTN_ASK, kb.LEGACY_ASK, kb.LEGACY_KB}))
async def ask_hint(message: Message, state: FSMContext, user: Optional[dict] = None) -> None:
    await state.clear()
    if not await subgate.allowed(message.bot, message.from_user.id, user):
        await subgate.show_wall(message.bot, message.chat.id, message.from_user.id, "button")
        return
    await message.answer(ASK_HINT, reply_markup=kb.MAIN_MENU)


@router.callback_query(F.data == "sub:check")
async def sub_check(callback: CallbackQuery, state: FSMContext) -> None:
    """«✅ Я подписался» — проверка без кэша; подписан → ответ на сохранённый вопрос."""
    uid = callback.from_user.id
    user = await database.get_user(uid)
    if not await subgate.allowed(callback.bot, uid, user, use_cache=False):
        await callback.answer()
        await callback.message.answer(subgate.NOT_YET)
        return
    await callback.answer()
    await state.clear()
    question = await subgate.pop_pending_question(uid)
    await database.log_event(uid, "sub_confirmed", json.dumps({"pending_question": bool(question)}))
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        logger.debug("Кнопки стены не убрались (сообщение старое)")
    await callback.message.answer(subgate.THANKS.format(limit=subgate.limit_text()), reply_markup=kb.MAIN_MENU)
    if question:
        await callback.message.answer(f"❓ {question}")
        await ask_and_reply(callback.bot, callback.message.chat.id, uid, user, question, check_sub=False)
    else:
        await callback.message.answer(ASK_HINT)


@router.message(F.text == kb.BTN_LIMIT)
@router.message(Command("limit"))
async def my_limit(message: Message, state: FSMContext, user: Optional[dict] = None) -> None:
    await state.clear()
    limit = daily_limit(message.from_user.id, user["role"] if user else None)
    if limit is None:
        await message.answer("У вас нет лимита на вопросы.")
        return
    used = await database.questions_used(message.from_user.id, shanghai_today())
    if config.SUB_GATE_ENABLED:
        if not await subgate.allowed(message.bot, message.from_user.id, user):
            await subgate.show_wall(message.bot, message.chat.id, message.from_user.id, "limit")
            return
        await message.answer(f"Использовано {used} из {limit}, обновится в 00:00 по Шанхаю")
        return
    left = until_reset()
    hours, minutes = left.seconds // 3600, left.seconds % 3600 // 60
    await message.answer(
        f"Сегодня использовано {used} из {limit}.\n"
        f"Лимит обновится в 00:00 по Шанхаю — через {hours} ч {minutes} мин."
    )


async def send_answer(bot: Bot, chat_id: int, text: str, footer: str = "", reply_markup=None) -> None:
    """HTML с экранированием; если Telegram отклонил разметку — повтор без неё (ТЗ 4.4)."""
    try:
        html = to_telegram_html(text) + (f"\n\n<i>{footer}</i>" if footer else "")
        await bot.send_message(chat_id, html, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        logger.warning("Telegram отклонил HTML (%s), отправляю без разметки", e)
        plain = to_plain(text) + (f"\n\n{footer}" if footer else "")
        await bot.send_message(chat_id, plain, reply_markup=reply_markup, parse_mode=None)


async def ask_and_reply(bot: Bot, chat_id: int, uid: int, user: Optional[dict], question: str,
                        via: str = "text", check_sub: bool = True) -> None:
    """Ядро вопроса для текста и для кнопки-подсказки: подписка (Э6) → лимит → ответ ИИ → кнопки (Э4).
    Без подписки — стена, вопрос сохраняется, лимит не списывается."""
    if check_sub and not await subgate.allowed(bot, uid, user):
        await subgate.show_wall(bot, chat_id, uid, via, question)
        return
    limit = daily_limit(uid, user["role"] if user else None)
    today = shanghai_today()
    used = None
    if limit is not None:
        used = await database.take_question(uid, today, limit)
        if used is None:
            await database.log_event(uid, "limit_hit", json.dumps({"limit": limit}))
            await bot.send_message(chat_id, LIMIT_EXHAUSTED, reply_markup=kb.manager_keyboard())
            return

    try:
        async with ChatActionSender.typing(bot=bot, chat_id=chat_id):
            result = await answer_question(uid, question)
    except Exception:
        logger.exception("answer_question failed for user %s", uid)
        if limit is not None:
            await database.refund_question(uid, today)
        await bot.send_message(chat_id, "Не получилось ответить — сервис временно недоступен. Попробуйте ещё раз через минуту.")
        return

    footer = ""
    if limit is not None and 0 < limit - used <= 3:
        footer = f"Осталось {plural_questions(limit - used)} на сегодня"
    markup = kb.answer_keyboard(
        result.message_id, result.intent, result.cta, result.service, result.suggestions,
        result.force_manager, result.answered_from_kb,
    )
    text = result.answer + (f"\n\n{kb.disclaimer_for(markup)}" if result.disclaimer else "")
    await send_answer(bot, chat_id, text, footer, markup)


@router.message(F.text, ~F.text.startswith("/"))
async def handle_question(message: Message, user: Optional[dict] = None) -> None:
    question = clean_question(message.text)
    if not question:  # пробелы / невидимые символы: не тратим лимит, не пишем в историю
        await message.answer("Напишите вопрос текстом — отвечу по нашим справочникам.")
        return
    await ask_and_reply(message.bot, message.chat.id, message.from_user.id, user, question)
