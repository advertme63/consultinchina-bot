from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup

BTN_ASK = "💬 Задать вопрос"
BTN_QUALIFY = "📊 Подходит ли мне компания в Китае"
BTN_MANAGER = "👤 Связаться с менеджером"
BTN_MATERIALS = "📄 Материалы"
BTN_LIMIT = "ℹ️ Мой лимит"
# Кнопки старого меню: у части пользователей клавиатура ещё прежняя
LEGACY_KB = "📚 Справочник"
LEGACY_TICKETS = "✉️ Обращения"
LEGACY_DOCS = "📄 Документы"

MENU_TEXTS = {BTN_ASK, BTN_QUALIFY, BTN_MANAGER, BTN_MATERIALS, BTN_LIMIT, LEGACY_KB, LEGACY_TICKETS, LEGACY_DOCS}

MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_ASK)],
        [KeyboardButton(text=BTN_QUALIFY)],
        [KeyboardButton(text=BTN_MANAGER), KeyboardButton(text=BTN_MATERIALS)],
        [KeyboardButton(text=BTN_LIMIT)],
    ],
    resize_keyboard=True,
)

CTA_QUALIFY_TEXT = "📊 Проверить, подходит ли мне"
CTA_MANAGER_TEXT = "👤 Связаться с менеджером"


def cta_keyboard(intent: str, cta: str) -> InlineKeyboardMarkup | None:
    """Кнопки под ответом по ТЗ 4.2."""
    rows = []
    if intent == "wants_calc" or cta == "qualify":
        rows.append([InlineKeyboardButton(text=CTA_QUALIFY_TEXT, callback_data="cta:qualify")])
    if intent == "wants_human" or cta == "manager":
        rows.append([InlineKeyboardButton(text=CTA_MANAGER_TEXT, callback_data="cta:manager")])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def manager_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=CTA_MANAGER_TEXT, callback_data="cta:manager")]]
    )


def is_not_menu_button(message: Message) -> bool:
    return message.text not in MENU_TEXTS
