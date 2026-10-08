"""my_chat_member (Э4): бота добавили / удалили / изменили права в группе → админу в личку.
Без этого обработчика Telegram такие апдейты боту не присылает (allowed_updates считаются по хендлерам)."""
import logging

from aiogram import Router
from aiogram.types import ChatMemberUpdated

from services.notify import chat_id_for, notify_admin

logger = logging.getLogger(__name__)
router = Router()

STATUS = {
    "member": "участник",
    "administrator": "администратор",
    "left": "вышел / удалён",
    "kicked": "заблокирован",
    "restricted": "ограничен",
    "creator": "владелец",
}


@router.my_chat_member()
async def bot_membership_changed(event: ChatMemberUpdated) -> None:
    if event.chat.type not in ("group", "supergroup"):
        return  # в личке — пользователь заблокировал / разблокировал бота; это не событие групп
    old, new = event.old_chat_member.status, event.new_chat_member.status
    known = {await chat_id_for("leads"): "«CinC Лиды»", await chat_id_for("reports"): "«CinC Отчёты»"}
    name = known.get(event.chat.id, f"«{event.chat.title}»")
    logger.warning("Бот в группе %s (%s): %s → %s", event.chat.title, event.chat.id, old, new)
    await notify_admin(
        event.bot,
        f"ℹ️ Бот в группе {name} (ID {event.chat.id}): {STATUS.get(old, old)} → {STATUS.get(new, new)}"
        + ("\n⚠️ Отправка в эту группу работать не будет." if new in ("left", "kicked") else ""),
    )
