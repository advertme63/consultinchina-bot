"""«Что вас привело?» и квалификация по сегментам (ТЗ 6.1–6.2, Э3.5). В лимит вопросов не входит."""
import html as html_lib
import json
import logging
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.chat_action import ChatActionSender

import database
import keyboards as kb
from services.segments import QUALIFIED, SEGMENTS, SKIPPED, python_block, step_options, verdict_text
from services.tg_format import to_plain, to_telegram_html
from states import QualifyStates

logger = logging.getLogger(__name__)
router = Router()
router.message.filter(F.chat.type == "private")

CANCEL_BTN = InlineKeyboardButton(text="✖️ Отмена", callback_data="q:cancel")
AFTER_SEGMENT = "Проверка займёт около минуты: несколько вопросов — и честный ответ, подходит ли вам это"
FREE_MODE = "Напишите свой вопрос — отвечу по нашим справочникам."


# --- «Что вас привело?» -------------------------------------------------------------

def segment_keyboard(mode: str) -> InlineKeyboardMarkup:
    """mode: start — после /start (выбор только сохраняет сегмент); q — сразу вопросы сценария."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=s.button, callback_data=f"seg:{s.key}:{mode}")]
                         for s in SEGMENTS.values()]
    )


async def ask_segment(message: Message, state: FSMContext, mode: str) -> None:
    await state.clear()
    await message.answer("Что вас привело?", reply_markup=segment_keyboard(mode))


@router.message(F.text == kb.BTN_QUALIFY)
async def qualify_from_menu(message: Message, state: FSMContext) -> None:
    await ask_segment(message, state, "q")


@router.callback_query(F.data == "cta:qualify")
async def qualify_from_cta(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await ask_segment(callback.message, state, "q")


@router.callback_query(F.data.startswith("seg:"))
async def segment_chosen(callback: CallbackQuery, state: FSMContext) -> None:
    _, key, mode = callback.data.split(":")
    seg = SEGMENTS.get(key)
    await callback.answer()
    if not seg:
        return
    await database.set_segment(callback.from_user.id, key)
    await callback.message.edit_text(f"Что вас привело? — <b>{seg.button}</b>")
    if key == "other":
        await state.clear()
        await callback.message.answer(FREE_MODE, reply_markup=kb.MAIN_MENU)
        return
    if mode == "start":
        await callback.message.answer(
            AFTER_SEGMENT,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="📊 Пройти проверку", callback_data=f"qstart:{key}")]]
            ),
        )
        await callback.message.answer("Или просто напишите свой вопрос.", reply_markup=kb.MAIN_MENU)
        return
    await start_questions(callback.message, state, callback.from_user.id, key)


@router.callback_query(F.data.startswith("qstart:"))
async def qstart(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await start_questions(callback.message, state, callback.from_user.id, callback.data.split(":", 1)[1])


# --- вопросы сценария -------------------------------------------------------------

async def _send_step(message: Message, state: FSMContext, segment: str, i: int) -> None:
    seg = SEGMENTS[segment]
    step = seg.steps[i]
    options = await step_options(step)
    await state.update_data(step=i, options=list(options))
    buttons = [InlineKeyboardButton(text=o, callback_data=f"q:{i}:{k}") for k, o in enumerate(options)]
    rows = [buttons[k:k + step.columns] for k in range(0, len(buttons), step.columns)]
    if step.skippable:
        rows.append([InlineKeyboardButton(text="Пропустить", callback_data=f"q:{i}:skip")])
    rows.append([CANCEL_BTN])
    await message.answer(
        f"{i + 1}/{len(seg.steps)}. {step.question}", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


async def start_questions(message: Message, state: FSMContext, user_id: int, segment: str) -> None:
    if segment not in {s.key for s in QUALIFIED}:
        await ask_segment(message, state, "q")
        return
    await state.clear()
    await state.set_state(QualifyStates.step)
    await state.update_data(segment=segment, answers={})
    await database.log_event(user_id, "qualify_start", json.dumps({"segment": segment}))
    await message.answer("Несколько коротких вопросов — и мы честно скажем, есть ли смысл. В лимит вопросов не входит.")
    await _send_step(message, state, segment, 0)


@router.callback_query(F.data == "q:cancel")
async def qualify_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("Проверку отменили. Можно вернуться к ней в любой момент из меню.", reply_markup=kb.MAIN_MENU)


@router.callback_query(QualifyStates.step, F.data.regexp(r"^q:\d+:(\d+|skip)$"))
async def qualify_answer(callback: CallbackQuery, state: FSMContext) -> None:
    _, i, choice = callback.data.split(":")
    data = await state.get_data()
    i = int(i)
    if i != data.get("step"):  # нажали кнопку старого шага
        await callback.answer("Ответьте на последний вопрос ниже.")
        return
    seg = SEGMENTS[data["segment"]]
    step = seg.steps[i]
    options = data.get("options") or []
    if choice == "skip":
        value = SKIPPED
    elif int(choice) < len(options):
        value = options[int(choice)]
    else:
        await callback.answer()
        return
    await callback.answer()
    title = re.split(r"[?.]", step.question)[0]
    await callback.message.edit_text(f"{i + 1}/{len(seg.steps)}. {title}? — <b>{value}</b>")
    await _save_and_next(callback.message, state, callback.from_user.id, step.key, value)


@router.callback_query(F.data.regexp(r"^q:\d+:(\d+|skip)$"))
async def qualify_stale_button(callback: CallbackQuery) -> None:
    """Кнопка анкеты, которой уже нет (анкета сброшена /start, отменена или бот перезапущен)."""
    await callback.answer("Анкета устарела, начните заново")
    await callback.message.answer(
        "Эта анкета уже неактуальна. Начните проверку заново:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="📊 Подходит ли мне", callback_data="cta:qualify")]]
        ),
    )


@router.message(QualifyStates.step, F.text, kb.is_not_menu_button, ~F.text.startswith("/"))
async def qualify_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    step = SEGMENTS[data["segment"]].steps[data["step"]]
    if step.options:
        await message.answer("Выберите вариант кнопкой выше или нажмите «Отмена».")
        return
    await _save_and_next(message, state, message.from_user.id, step.key, message.text.strip()[:100])


def lead_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=kb.CTA_REVIEW_TEXT, callback_data="lead:start")]]
    )


async def verdict_message(segment: str, answers: dict, v: str) -> tuple[str, str]:
    """Текст итога (Claude) и блок с цифрами (Python). → (html, plain)"""
    seg = SEGMENTS[segment]
    try:
        text, _, _ = await verdict_text(segment, answers, v)
    except Exception:
        logger.exception("verdict_text failed")
        text = f"**{seg.labels[v]}**\n\nПодробно разберём вашу ситуацию с Валерием Загурским — кнопка ниже."
    try:
        block = await python_block(segment, v, answers)
    except Exception:
        logger.exception("python_block failed")
        block = ""
    html = to_telegram_html(text) + (f"\n\n{block}" if block else "")
    plain_block = html_lib.unescape(re.sub(r"<[^>]+>", "", block))
    plain = to_plain(text) + (f"\n\n{plain_block}" if plain_block else "")
    return html, plain


async def _save_and_next(message: Message, state: FSMContext, user_id: int, key: str, value: str) -> None:
    data = await state.get_data()
    segment = data["segment"]
    seg = SEGMENTS[segment]
    answers = {**data.get("answers", {}), key: value}
    nxt = data["step"] + 1
    if nxt < len(seg.steps):
        await state.update_data(answers=answers)
        await _send_step(message, state, segment, nxt)
        return

    await state.clear()
    v = seg.verdict(answers)
    await database.log_event(
        user_id, "qualify_done", json.dumps({"segment": segment, "verdict": v, "answers": answers}, ensure_ascii=False)
    )
    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        html, plain = await verdict_message(segment, answers, v)
    try:
        await message.answer(html, reply_markup=lead_keyboard())
    except Exception:
        await message.answer(plain, reply_markup=lead_keyboard(), parse_mode=None)
