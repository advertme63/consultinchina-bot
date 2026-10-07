from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import database
from config import config
from keyboards import MAIN_MENU, is_not_menu_button
from states import TicketStates

router = Router()

CATEGORIES = ["регистрация", "бухгалтерия", "маркетплейсы", "другое"]


def categories_keyboard() -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=c, callback_data=f"ticket_cat:{c}")] for c in CATEGORIES]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(F.text == "✉️ Обращения")
async def enter_tickets(message: Message, state: FSMContext) -> None:
    await state.set_state(TicketStates.choosing_category)
    await message.answer("Выберите категорию обращения:", reply_markup=categories_keyboard())


@router.callback_query(TicketStates.choosing_category, F.data.startswith("ticket_cat:"))
async def choose_category(callback: CallbackQuery, state: FSMContext) -> None:
    category = callback.data.split(":", 1)[1]
    await state.update_data(category=category)
    await state.set_state(TicketStates.entering_text)
    await callback.message.edit_text(f"Категория: {category}\nОпишите ваш вопрос одним сообщением.")
    await callback.answer()


@router.message(TicketStates.entering_text, F.text, is_not_menu_button)
async def save_ticket(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    ticket_id = await database.create_ticket(message.from_user.id, data["category"], message.text)
    await state.clear()
    await message.answer("Спасибо! Обращение принято, мы свяжемся с вами.", reply_markup=MAIN_MENU)

    try:
        await message.bot.send_message(
            config.ADMIN_TELEGRAM_ID,
            f"🆕 Новое обращение #{ticket_id}\n"
            f"От: {message.from_user.full_name} (@{message.from_user.username or '—'}, "
            f"ID {message.from_user.id})\n"
            f"Категория: {data['category']}\n\n{message.text}",
        )
    except Exception:
        pass
