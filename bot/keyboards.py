from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

MENU_TEXTS = {"📚 Справочник", "✉️ Обращения", "📄 Документы"}

MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📚 Справочник")],
        [KeyboardButton(text="✉️ Обращения"), KeyboardButton(text="📄 Документы")],
    ],
    resize_keyboard=True,
)


def is_not_menu_button(message: Message) -> bool:
    return message.text not in MENU_TEXTS
