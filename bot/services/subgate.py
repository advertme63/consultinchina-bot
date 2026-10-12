"""Справочник по подписке на канал (Э6): проверка getChatMember, кэш 10 мин, стена, отложенный вопрос.

Без проверки: админ, роль client, пришедшие по ?start=call, тестовые. Сбой Telegram → считаем подписанным + лог.
Вопрос, написанный до подписки, лежит в payload события sub_gate_shown — после «✅ Я подписался» на него отвечаем."""
import json
import logging
import time
from typing import Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import database
from config import config

logger = logging.getLogger(__name__)

CACHE_TTL = 600  # 10 мин
SUBSCRIBED = {"member", "administrator", "creator"}
# Тестовые (source = 'test' / ID < 0) — без проверки; e2e-симулятор выключает, чтобы проверять саму стену
TESTS_EXEMPT = True
PENDING_MAX_AGE = "24 hours"

_cache: dict[int, tuple[bool, float]] = {}


def channel_url() -> str:
    return f"https://t.me/{config.SUB_CHANNEL.lstrip('@')}"


def limit_text() -> str:
    return f"до {config.DAILY_LIMIT_GUEST} вопросов в день"


def wall_text() -> str:
    return (f"📚 Справочник бесплатный — нужна только подписка на наш канал {config.SUB_CHANNEL}.\n"
            "Подпишитесь и нажмите «✅ Я подписался».")


def limit_wall_text() -> str:
    return f"Справочник открывается после подписки на {config.SUB_CHANNEL}"


THANKS = "Спасибо! Справочник открыт — {limit}."
NOT_YET = "Пока не видим подписку. Подпишитесь и нажмите ещё раз."


def wall_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📢 Перейти в канал", url=channel_url()),
        InlineKeyboardButton(text="✅ Я подписался", callback_data="sub:check"),
    ]])


async def is_exempt(uid: int, user) -> bool:
    if uid == config.ADMIN_TELEGRAM_ID:
        return True
    if user is not None and user["role"] == "client":
        return True
    source = user["source"] if user is not None else None
    if TESTS_EXEMPT and (uid < 0 or source == "test"):
        return True
    if source == "call":
        return True
    # первая метка источника не перезаписывается: «call» после другой метки — только в событии start
    async with database.pool().acquire() as conn:
        return bool(await conn.fetchval(
            "SELECT true FROM events WHERE telegram_id = $1 AND type = 'start' AND payload->>'source' = 'call' LIMIT 1",
            uid))


async def is_subscribed(bot: Bot, uid: int, use_cache: bool = True) -> bool:
    """getChatMember по каналу. Ошибка Telegram → True (не наказываем клиента за наш сбой) + лог + событие."""
    if use_cache:
        hit = _cache.get(uid)
        if hit and time.monotonic() - hit[1] < CACHE_TTL:
            return hit[0]
    try:
        member = await bot.get_chat_member(config.SUB_CHANNEL, uid)
    except Exception as e:
        logger.warning("Подписка: getChatMember %s для %s не удался (%s: %s) — пропускаем как подписанного",
                       config.SUB_CHANNEL, uid, type(e).__name__, e)
        await database.log_event(uid, "sub_check_failed", json.dumps({"error": f"{type(e).__name__}: {e}"[:300]}))
        return True
    status = str(getattr(member, "status", ""))
    ok = status in SUBSCRIBED or (status == "restricted" and bool(getattr(member, "is_member", False)))
    _cache[uid] = (ok, time.monotonic())
    return ok


async def allowed(bot: Bot, uid: int, user, use_cache: bool = True) -> bool:
    """Можно ли пользоваться справочником сейчас."""
    if not config.SUB_GATE_ENABLED or await is_exempt(uid, user):
        return True
    return await is_subscribed(bot, uid, use_cache)


async def show_wall(bot: Bot, chat_id: int, uid: int, via: str, question: Optional[str] = None) -> None:
    """via: button / text / suggestion / limit. Вопрос (если есть) сохраняется в событии — лимит не списывается."""
    await database.log_event(uid, "sub_gate_shown", json.dumps({"via": via, "question": question}, ensure_ascii=False))
    text = limit_wall_text() if via == "limit" else wall_text()
    await bot.send_message(chat_id, text, reply_markup=wall_keyboard())


async def pop_pending_question(uid: int) -> Optional[str]:
    """Последний вопрос со стены после последнего sub_confirmed (не старше суток). Вызывать до записи sub_confirmed."""
    async with database.pool().acquire() as conn:
        return await conn.fetchval(
            f"""
            SELECT e.payload->>'question' FROM events e
            WHERE e.telegram_id = $1 AND e.type = 'sub_gate_shown' AND e.payload->>'question' IS NOT NULL
              AND e.created_at > now() - interval '{PENDING_MAX_AGE}'
              AND e.created_at > COALESCE((SELECT max(created_at) FROM events
                                           WHERE telegram_id = $1 AND type = 'sub_confirmed'), '-infinity')
            ORDER BY e.created_at DESC, e.id DESC LIMIT 1
            """,
            uid,
        )
