"""Учёт всех вызовов Claude (Э4): токены по видам и стоимость по ценам из .env."""
import logging
from dataclasses import dataclass
from typing import Optional

import database
from config import config

logger = logging.getLogger(__name__)


@dataclass
class Usage:
    input: int = 0  # «свежий» вход, без кеша
    cache_read: int = 0
    cache_write_5m: int = 0
    cache_write_1h: int = 0
    output: int = 0

    @property
    def total_in(self) -> int:
        return self.input + self.cache_read + self.cache_write_5m + self.cache_write_1h

    @property
    def cost_usd(self) -> float:
        return (
            self.input * config.PRICE_INPUT
            + self.cache_read * config.PRICE_CACHE_READ
            + self.cache_write_5m * config.PRICE_CACHE_WRITE_5M
            + self.cache_write_1h * config.PRICE_CACHE_WRITE_1H
            + self.output * config.PRICE_OUTPUT
        ) / 1_000_000


def from_api(u) -> Usage:
    """usage из ответа Anthropic: запись в кеш — раздельно 5 мин / 1 ч, если SDK отдал разбивку."""
    read = getattr(u, "cache_read_input_tokens", 0) or 0
    write_total = getattr(u, "cache_creation_input_tokens", 0) or 0
    cc = getattr(u, "cache_creation", None)
    w5 = getattr(cc, "ephemeral_5m_input_tokens", None) if cc else None
    w1 = getattr(cc, "ephemeral_1h_input_tokens", None) if cc else None
    if w5 is None and w1 is None:
        w5, w1 = write_total, 0  # бот ставит только 5-минутный кеш
    return Usage(u.input_tokens or 0, read, w5 or 0, w1 or 0, u.output_tokens or 0)


async def record(usage: Usage, purpose: str, telegram_id: Optional[int] = None) -> None:
    """Пишет вызов в llm_usage. Ошибка записи не должна ломать ответ клиенту."""
    try:
        await database.save_llm_usage(
            telegram_id, purpose, config.CLAUDE_MODEL, usage.input, usage.cache_read,
            usage.cache_write_5m, usage.cache_write_1h, usage.output, usage.cost_usd,
        )
    except Exception:
        logger.exception("Не удалось записать llm_usage (%s)", purpose)
