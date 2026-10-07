"""«📊 Подходит ли мне компания в Китае»: 6 вопросов и итог (ТЗ 6.1, 6.2). В лимит вопросов не входит."""
import json
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.chat_action import ChatActionSender

import database
import keyboards as kb
from services.qualify import SKIPPED, STEPS, VERDICT_LABELS, verdict, verdict_text
from services.tg_format import to_plain, to_telegram_html
from states import QualifyStates

logger = logging.getLogger(__name__)
router = Router()
router.message.filter(F.chat.type == "private")

CANCEL_BTN = InlineKeyboardButton(text="✖️ Отмена", callback_data="q:cancel")


def step_keyboard(i: int) -> InlineKeyboardMarkup:
    step = STEPS[i]
    rows = [[InlineKeyboardButton(text=o, callback_data=f"q:{i}:{k}")] for k, o in enumerate(step.options)]
    if not step.options:
        rows.append([InlineKeyboardButton(text="Пропустить", callback_data=f"q:{i}:skip")])
    rows.append([CANCEL_BTN])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def lead_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=kb.CTA_REVIEW_TEXT, callback_data="lead:start")]]
    )


async def start_qualify(message: Message, state: FSMContext, user_id: int) -> None:
    await state.clear()
    await state.set_state(QualifyStates.step)
    await state.update_data(step=0, answers={})
    await database.log_event(user_id, "qualify_start")
    await message.answer(
        "Шесть коротких вопросов — и мы честно скажем, есть ли смысл в китайской компании. Это не входит в лимит вопросов."
    )
    await message.answer(f"1/{len(STEPS)}. {STEPS[0].question}", reply_markup=step_keyboard(0))


@router.message(F.text == kb.BTN_QUALIFY)
async def qualify_from_menu(message: Message, state: FSMContext) -> None:
    await start_qualify(message, state, message.from_user.id)


@router.callback_query(F.data == "cta:qualify")
async def qualify_from_cta(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await start_qualify(callback.message, state, callback.from_user.id)


@router.callback_query(F.data == "q:cancel")
async def qualify_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("Проверку отменили. Можно вернуться к ней в любой момент из меню.", reply_markup=kb.MAIN_MENU)


@router.callback_query(QualifyStates.step, F.data.startswith("q:"))
async def qualify_answer(callback: CallbackQuery, state: FSMContext) -> None:
    _, i, choice = callback.data.split(":")
    data = await state.get_data()
    i = int(i)
    if i != data.get("step"):  # нажали кнопку старого шага
        await callback.answer("Ответьте на последний вопрос ниже.")
        return
    step = STEPS[i]
    value = SKIPPED if choice == "skip" else step.options[int(choice)]
    await callback.answer()
    await callback.message.edit_text(f"{i + 1}/{len(STEPS)}. {step.question.split('?')[0]}? — <b>{value}</b>")
    await _save_and_next(callback.message, state, callback.from_user.id, step.key, value)


@router.message(QualifyStates.step, F.text, kb.is_not_menu_button, ~F.text.startswith("/"))
async def qualify_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    step = STEPS[data["step"]]
    if step.options:
        await message.answer("Выберите вариант кнопкой выше или нажмите «Отмена».")
        return
    await _save_and_next(message, state, message.from_user.id, step.key, message.text.strip()[:100])


async def _save_and_next(message: Message, state: FSMContext, user_id: int, key: str, value: str) -> None:
    data = await state.get_data()
    answers = {**data.get("answers", {}), key: value}
    nxt = data["step"] + 1
    if nxt < len(STEPS):
        await state.update_data(step=nxt, answers=answers)
        await message.answer(f"{nxt + 1}/{len(STEPS)}. {STEPS[nxt].question}", reply_markup=step_keyboard(nxt))
        return

    await state.clear()
    v = verdict(answers)
    await database.log_event(user_id, "qualify_done", json.dumps({"verdict": v, "answers": answers}, ensure_ascii=False))
    try:
        async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
            text, _, _ = await verdict_text(answers, v)
    except Exception:
        logger.exception("verdict_text failed")
        text = f"**{VERDICT_LABELS[v]}**\n\nПодробно разберём вашу ситуацию с Валерием Загурским — кнопка ниже."
    try:
        await message.answer(to_telegram_html(text), reply_markup=lead_keyboard())
    except Exception:
        await message.answer(to_plain(text), reply_markup=lead_keyboard(), parse_mode=None)
