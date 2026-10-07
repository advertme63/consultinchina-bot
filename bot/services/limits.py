"""Суточные лимиты вопросов к ИИ (ТЗ 2). Сутки — по Шанхаю (UTC+8, перехода на летнее время нет)."""
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from config import config

SHANGHAI_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


def shanghai_now() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def shanghai_today() -> date:
    return shanghai_now().date()


def until_reset() -> timedelta:
    now = shanghai_now()
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - now


def daily_limit(telegram_id: int, role: Optional[str]) -> Optional[int]:
    """None — без лимита (админ)."""
    if telegram_id == config.ADMIN_TELEGRAM_ID:
        return None
    return config.DAILY_LIMIT_CLIENT if role == "client" else config.DAILY_LIMIT_GUEST


def plural_questions(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} вопрос"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} вопроса"
    return f"{n} вопросов"
