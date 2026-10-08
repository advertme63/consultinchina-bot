from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup

BTN_ASK = "💬 Задать вопрос"
BTN_QUALIFY = "📊 Подходит ли мне компания в Китае"
BTN_MANAGER = "👤 Связаться со специалистом"
# Прежняя надпись: у части пользователей в Telegram ещё старая клавиатура
LEGACY_MANAGER = "👤 Связаться с менеджером"
BTN_MATERIALS = "📄 Материалы"
BTN_LIMIT = "ℹ️ Мой лимит"
# Кнопки старого меню: у части пользователей клавиатура ещё прежняя
LEGACY_KB = "📚 Справочник"
LEGACY_TICKETS = "✉️ Обращения"
LEGACY_DOCS = "📄 Документы"

MENU_TEXTS = {BTN_ASK, BTN_QUALIFY, BTN_MANAGER, BTN_MATERIALS, BTN_LIMIT, LEGACY_KB, LEGACY_TICKETS, LEGACY_DOCS, LEGACY_MANAGER}

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
CTA_MANAGER_TEXT = "👤 Связаться со специалистом"
CTA_REVIEW_TEXT = "👤 Разбор со специалистом"


def cta_keyboard(intent: str, cta: str, force_manager: bool = False) -> InlineKeyboardMarkup | None:
    """Кнопки под ответом по ТЗ 4.2. force_manager — вопрос о цене / оплате регистрации."""
    rows = []
    if intent == "wants_calc" or cta == "qualify":
        rows.append([InlineKeyboardButton(text=CTA_QUALIFY_TEXT, callback_data="cta:qualify")])
    if intent == "wants_human" or cta == "manager" or force_manager:
        rows.append([InlineKeyboardButton(text=CTA_MANAGER_TEXT, callback_data="cta:manager")])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


QUESTION_SPECIALIST_TEXT = "👤 Вопрос специалисту"
ORDER_PREFIX = "📝 Заказать: "
SUGGESTION_PREFIX = "💬 "


def answer_keyboard(
    message_id: int,
    intent: str,
    cta: str,
    service: str | None,
    suggestions: list[str] | None,
    force_manager: bool,
    answered_from_kb: bool,
    show_suggestions: bool = True,
    show_rating: bool = True,
) -> InlineKeyboardMarkup | None:
    """Кнопки под ответом ИИ (Э4): ряд 1 — действие, ряд 2 — подсказки, ряд 3 — 👍/👎."""
    from services.catalog import service_title

    rows: list[list[InlineKeyboardButton]] = []
    action: list[InlineKeyboardButton] = []
    if intent == "off_topic":
        pass
    elif not answered_from_kb:  # «нет в материалах» — только менеджер (решение Ивана 08.10)
        action.append(InlineKeyboardButton(text=CTA_MANAGER_TEXT, callback_data="cta:manager"))
    elif service and service != "none" and service_title(service):
        action.append(InlineKeyboardButton(text=ORDER_PREFIX + service_title(service),
                                           callback_data=f"order:{service}:{message_id}"))
        action.append(InlineKeyboardButton(text=QUESTION_SPECIALIST_TEXT, callback_data="cta:manager"))
    else:
        if intent == "wants_calc" or cta == "qualify":
            action.append(InlineKeyboardButton(text=CTA_QUALIFY_TEXT, callback_data="cta:qualify"))
        if intent == "wants_human" or cta == "manager" or force_manager:
            action.append(InlineKeyboardButton(text=CTA_MANAGER_TEXT, callback_data="cta:manager"))
    if action:
        rows.append(action)
    if show_suggestions and suggestions:
        for i, text in enumerate(suggestions[:2]):  # каждая подсказка — отдельной кнопкой (до 50 симв. в ряд по одной)
            rows.append([InlineKeyboardButton(text=SUGGESTION_PREFIX + text, callback_data=f"sug:{message_id}:{i}")])
    if show_rating:
        rows.append([
            InlineKeyboardButton(text="👍", callback_data=f"rate:{message_id}:1"),
            InlineKeyboardButton(text="👎", callback_data=f"rate:{message_id}:-1"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


DISCLAIMER_WITH_ACTION = "Это общая информация. Решение по вашей ситуации — с нашим специалистом, кнопка ниже."
DISCLAIMER_WITH_SUGGESTIONS = "Это общая информация. Ниже — похожие вопросы по теме."
DISCLAIMER_PLAIN = "Это общая информация — ваша ситуация может отличаться."


def disclaimer_for(markup: InlineKeyboardMarkup | None) -> str:
    """Концовка юридического / налогового ответа по раскладке кнопок (решение Ивана 08.10)."""
    rows = markup.inline_keyboard if markup else []
    has_action = any(b.callback_data.startswith(("cta:", "order:")) for r in rows for b in r)
    has_suggestions = any(b.callback_data.startswith("sug:") for r in rows for b in r)
    if has_action:
        return DISCLAIMER_WITH_ACTION
    if has_suggestions:
        return DISCLAIMER_WITH_SUGGESTIONS
    return DISCLAIMER_PLAIN


def keyboard_for_message(row, show_suggestions: bool = True, show_rating: bool = True) -> InlineKeyboardMarkup | None:
    """Та же клавиатура, собранная из строки messages (для правки после нажатий)."""
    from services.answer import needs_manager_button

    return answer_keyboard(
        row["id"], row["intent"] or "question", row["cta"] or "none", row["service"], row["suggestions"],
        needs_manager_button(row["question"] or ""), bool(row["answered_from_kb"]),
        show_suggestions=show_suggestions, show_rating=show_rating and row["rating"] is None,
    )


def manager_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=CTA_MANAGER_TEXT, callback_data="cta:manager")]]
    )


def is_not_menu_button(message: Message) -> bool:
    return message.text not in MENU_TEXTS
