import logging
from typing import Optional

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from keyboards import is_not_menu_button
from services.rag import answer_question
from states import KnowledgeStates

logger = logging.getLogger(__name__)
router = Router()


@router.message(F.text == "📚 Справочник")
async def enter_knowledge(message: Message, state: FSMContext) -> None:
    await state.set_state(KnowledgeStates.active)
    await message.answer(
        "Задайте вопрос — отвечу на основе материалов ConsultInChina. "
        "Чтобы выйти в меню, нажмите любую другую кнопку."
    )


@router.message(KnowledgeStates.active, F.text, is_not_menu_button)
async def handle_question(message: Message, state: FSMContext, user: Optional[dict]) -> None:
    if not user or user["role"] == "none":
        await message.answer(
            "Справочник доступен по пробному или полному доступу. "
            "Напишите в раздел «✉️ Обращения», и мы свяжемся с вами."
        )
        return

    await message.bot.send_chat_action(message.chat.id, "typing")
    try:
        reply, found = await answer_question(message.text, topic_hint=None)
    except Exception:
        logger.exception("Knowledge base query failed for user %s", message.from_user.id)
        await message.answer(
            "Сервис справочника временно недоступен. Попробуйте позже или создайте обращение."
        )
        return

    if not found:
        reply += "\n\nНе нашли ответ? Создайте обращение в разделе «✉️ Обращения»."
    await message.answer(reply)
