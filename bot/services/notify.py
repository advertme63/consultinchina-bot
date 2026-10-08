"""Отправка в служебные группы (Э4): «CinC Лиды» (leads) и «CinC Отчёты» (reports).
migrate_to_chat_id → новый ID в settings + повтор; любая ошибка → лог и админу в личку (ТЗ 6.4);
длинный текст делится по 4096 символов."""
import logging
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramMigrateToChat
from aiogram.types import Message

import database
from config import config

logger = logging.getLogger(__name__)

GROUPS = {
    "leads": ("leads_chat_id", "LEADS_CHAT_ID", "лидов"),
    "reports": ("reports_chat_id", "REPORTS_CHAT_ID", "отчётов"),
}
TG_LIMIT = 4096


async def chat_id_for(kind: str) -> Optional[int]:
    key, env, _ = GROUPS[kind]
    saved = await database.get_setting(key)
    return int(saved) if saved else getattr(config, env)


async def notify_admin(bot: Bot, text: str) -> None:
    try:
        await bot.send_message(config.ADMIN_TELEGRAM_ID, text[:TG_LIMIT], parse_mode=None)
    except Exception:
        logger.exception("Не удалось написать админу")


def split_text(text: str, limit: int = TG_LIMIT) -> list[str]:
    """Режем по абзацам, затем по строкам; кусок не длиннее limit."""
    parts, cur = [], ""
    for block in text.split("\n"):
        while len(block) > limit:
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(block[:limit])
            block = block[limit:]
        candidate = f"{cur}\n{block}" if cur else block
        if len(candidate) > limit:
            parts.append(cur)
            cur = block
        else:
            cur = candidate
    if cur:
        parts.append(cur)
    return parts or [""]


async def send_to_group(bot: Bot, kind: str, text: str, **kwargs) -> Optional[Message]:
    """Возвращает первое отправленное сообщение (для group_message_id) или None."""
    key, env, title = GROUPS[kind]
    chat_id = await chat_id_for(kind)
    if not chat_id:
        logger.error("%s не задан, сообщение в группу %s не отправлено", env, title)
        await notify_admin(bot, f"⚠️ {env} не задан — сообщение не ушло в группу {title}:\n\n{text}")
        return None
    chunks = split_text(text)
    first: Optional[Message] = None
    for n, chunk in enumerate(chunks):
        extra = kwargs if n == len(chunks) - 1 else {k: v for k, v in kwargs.items() if k != "reply_markup"}
        for _ in range(2):
            try:
                sent = await bot.send_message(chat_id, chunk, **extra)
                first = first or sent
                break
            except TelegramMigrateToChat as e:
                logger.warning("Группа %s переехала: %s → %s", title, chat_id, e.migrate_to_chat_id)
                chat_id = e.migrate_to_chat_id
                await database.set_setting(key, str(chat_id))
                await notify_admin(bot, f"ℹ️ Группа {title} стала супергруппой, новый ID {chat_id} сохранён в settings.")
            except Exception as e:
                logger.exception("Не удалось отправить в группу %s %s", title, chat_id)
                await notify_admin(
                    bot, f"⚠️ Не удалось отправить в группу {title} ({type(e).__name__}: {e}).\n\nТекст:\n{text[:3000]}"
                )
                return first
    return first
