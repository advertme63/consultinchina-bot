"""Кнопки под ответами ИИ (Э4): подсказка (sug:), заказ услуги (order:), оценка (rate:) и комментарий к 👎."""
import json
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import database
import keyboards as kb
from handlers.leads import start_order
from handlers.questions import ask_and_reply
from services.catalog import SERVICES
from states import RatingStates

logger = logging.getLogger(__name__)
router = Router()
router.message.filter(F.chat.type == "private")

FEEDBACK_ASK = "Что не так? Напишите коротко или нажмите «Пропустить»."
FEEDBACK_THANKS = "Спасибо, учтём."


async def _edit_markup(callback: CallbackQuery, row, show_suggestions: bool) -> None:
    try:
        await callback.message.edit_reply_markup(reply_markup=kb.keyboard_for_message(row, show_suggestions=show_suggestions))
    except Exception:
        logger.debug("Кнопки не обновились (сообщение старое или не изменилось)")


# --- подсказка: как обычный вопрос клиента (лимит −1) ----------------------------

@router.callback_query(F.data.regexp(r"^sug:\d+:[01]$"))
async def suggestion_click(callback: CallbackQuery, state: FSMContext) -> None:
    _, mid, i = callback.data.split(":")
    row = await database.get_message(int(mid))
    if not row or row["telegram_id"] != callback.from_user.id or not row["suggestions"] or int(i) >= len(row["suggestions"]):
        await callback.answer("Подсказка устарела — напишите вопрос своими словами.")
        return
    text = row["suggestions"][int(i)]
    await callback.answer()
    await state.clear()
    await _edit_markup(callback, row, show_suggestions=False)  # ряд подсказок убираем, 👍/👎 остаётся
    await database.log_event(callback.from_user.id, "suggestion_click", json.dumps({"message_id": int(mid), "i": int(i)}))
    await callback.message.answer(f"❓ {text}")
    user = await database.get_user(callback.from_user.id)
    await ask_and_reply(callback.bot, callback.message.chat.id, callback.from_user.id, user, text)


# --- «📝 Заказать: <услуга>»: ключ услуги — в самой кнопке ---------------------------

@router.callback_query(F.data.regexp(r"^order:[a-z_]+:\d+$"))
async def order_click(callback: CallbackQuery, state: FSMContext) -> None:
    _, service, mid = callback.data.split(":")
    await callback.answer()
    if service not in SERVICES:
        await callback.message.answer("Эта кнопка устарела. Напишите, какая услуга нужна, — передадим Валерию Загурскому.")
        return
    await start_order(callback.message, state, callback.from_user.first_name, service, int(mid))


# --- 👍 / 👎 и комментарий ----------------------------------------------------------

@router.callback_query(F.data.regexp(r"^rate:\d+:(1|-1)$"))
async def rate_click(callback: CallbackQuery, state: FSMContext) -> None:
    _, mid, value = callback.data.split(":")
    mid, value = int(mid), int(value)
    ok = await database.set_rating(mid, callback.from_user.id, value)
    if not ok:
        await callback.answer("Оценка уже учтена.")
        return
    await callback.answer("Спасибо за оценку!")
    row = await database.get_message(mid)
    if row:
        await _edit_markup(callback, row, show_suggestions=True)  # 👍/👎 убираются (rating уже стоит)
    if value == -1:
        await state.set_state(RatingStates.feedback)
        await state.update_data(feedback_mid=mid)
        await callback.message.answer(
            FEEDBACK_ASK,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="Пропустить", callback_data=f"fb_skip:{mid}")]]
            ),
        )


@router.callback_query(F.data.startswith("fb_skip:"))
async def feedback_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if await state.get_state() == RatingStates.feedback.state:
        await state.clear()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


@router.message(RatingStates.feedback, F.text, kb.is_not_menu_button, ~F.text.startswith("/"))
async def feedback_text(message: Message, state: FSMContext) -> None:
    """Комментарий к 👎 — в messages.feedback; в лимит вопросов не входит."""
    data = await state.get_data()
    await state.clear()
    if data.get("feedback_mid"):
        await database.set_feedback(data["feedback_mid"], message.from_user.id, message.text.strip()[:1000])
    await message.answer(FEEDBACK_THANKS)
