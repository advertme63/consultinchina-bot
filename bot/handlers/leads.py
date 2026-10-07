"""Заявка Валерию Загурскому (ТЗ 6.3): имя → телефон → карточка в «CinC Лиды». В лимит не входит."""
import logging
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, KeyboardButton, Message, ReplyKeyboardMarkup

import database
import keyboards as kb
from services.leads import CLIENT_THANKS, submit_lead
from states import LeadStates

logger = logging.getLogger(__name__)
router = Router()
router.message.filter(F.chat.type == "private")

BTN_SKIP = "Пропустить"
BTN_CANCEL = "✖️ Отмена"


def name_keyboard(first_name: str | None) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=first_name)]] if first_name else []
    rows.append([KeyboardButton(text=BTN_CANCEL)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


PHONE_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📱 Поделиться контактом", request_contact=True)],
        [KeyboardButton(text=BTN_SKIP)],
        [KeyboardButton(text=BTN_CANCEL)],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
)


async def start_lead(message: Message, state: FSMContext, first_name: str | None) -> None:
    await state.clear()
    await state.set_state(LeadStates.name)
    await message.answer(
        "Передадим ваш вопрос Валерию Загурскому. Как к вам обращаться?", reply_markup=name_keyboard(first_name)
    )


@router.message(F.text.in_({kb.BTN_MANAGER, kb.LEGACY_TICKETS}))
async def lead_from_menu(message: Message, state: FSMContext) -> None:
    await start_lead(message, state, message.from_user.first_name)


@router.callback_query(F.data.in_({"cta:manager", "lead:start"}))
async def lead_from_button(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await start_lead(callback.message, state, callback.from_user.first_name)


@router.message(LeadStates.name, F.text == BTN_CANCEL)
@router.message(LeadStates.phone, F.text == BTN_CANCEL)
async def lead_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Хорошо, отменили. Если передумаете — кнопка «👤 Связаться с менеджером» в меню.", reply_markup=kb.MAIN_MENU)


@router.message(LeadStates.name, F.text, kb.is_not_menu_button, ~F.text.startswith("/"))
async def lead_name(message: Message, state: FSMContext) -> None:
    await state.update_data(name=message.text.strip()[:100])
    await state.set_state(LeadStates.phone)
    await message.answer(
        "Оставьте телефон — или нажмите «Пропустить», Валерию достаточно вашего Telegram.", reply_markup=PHONE_KEYBOARD
    )


@router.message(LeadStates.phone, F.contact)
async def lead_phone_contact(message: Message, state: FSMContext) -> None:
    await _finish(message, state, message.contact.phone_number)


@router.message(LeadStates.phone, F.text, kb.is_not_menu_button, ~F.text.startswith("/"))
async def lead_phone_text(message: Message, state: FSMContext) -> None:
    phone = None if message.text == BTN_SKIP else message.text.strip()[:40]
    await _finish(message, state, phone)


async def _finish(message: Message, state: FSMContext, phone: str | None) -> None:
    data = await state.get_data()
    await state.clear()
    user = await database.get_user(message.from_user.id)
    await message.answer(CLIENT_THANKS, reply_markup=kb.MAIN_MENU)
    try:
        await submit_lead(message.bot, user, data.get("name") or message.from_user.first_name or "—", phone)
    except Exception:
        logger.exception("submit_lead failed for %s", message.from_user.id)


# --- группа «CinC Лиды»: «Взял в работу» ------------------------------------------

@router.callback_query(F.data.startswith("lead_take:"))
async def lead_take(callback: CallbackQuery) -> None:
    lead_id = int(callback.data.split(":", 1)[1])
    who = f"@{callback.from_user.username}" if callback.from_user.username else callback.from_user.full_name
    lead = await database.take_lead(lead_id, callback.from_user.id)
    if not lead:
        current = await database.get_lead(lead_id)
        await callback.answer("Заявка уже в работе." if current else "Заявка не найдена.", show_alert=True)
        return
    await callback.answer("Заявка ваша.")
    try:
        await callback.message.edit_text(
            callback.message.html_text + f"\n\n✅ <b>В работе:</b> {escape(who)}", reply_markup=None
        )
    except Exception:
        logger.exception("Не удалось обновить карточку заявки #%s", lead_id)
