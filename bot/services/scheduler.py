"""Планировщик задач Э4 на asyncio (без новых зависимостей). Время — Шанхай (UTC+8).

Задачи: 09:00 отчёт за вчера → «CinC Отчёты»; 10:30 автоочистка (после бэкапа 03:15 по серверу);
19:00 сводка новых вопросов → «CinC Лиды»; пн 10:00 анализ недели → «CinC Отчёты».
Защита от двойной отправки: settings['job:<имя>:last_run'] = дата Шанхая, ставится ДО запуска.
Пропуск догоняется, только если опоздание ≤ MAX_LATE (рестарт в 09:05 — отчёт уйдёт; выкладка в 15:00 — нет).
Запускается только из main.py (SCHEDULER_ENABLED=1); тесты и e2e его не стартуют."""
import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Awaitable, Callable, Optional

from aiogram import Bot

import database
from services import notify
from services.limits import SHANGHAI_TZ, shanghai_now

logger = logging.getLogger(__name__)
TICK_SECONDS = 30
MAX_LATE = timedelta(hours=3)
RETENTION = "12 months"


@dataclass
class Job:
    name: str
    at: time
    run: Callable[[Bot], Awaitable[None]]
    weekday: Optional[int] = None  # 0 = понедельник


async def job_daily_report(bot: Bot) -> None:
    from services.stats import daily_report

    await notify.send_to_group(bot, "reports", await daily_report(), parse_mode=None)


async def job_digest(bot: Bot) -> None:
    from services.digest import build_digest

    text = await build_digest()
    if text:  # нет новых вопросов — не отправляем (ТЗ 7.1)
        await notify.send_to_group(bot, "leads", text, parse_mode=None)


async def job_weekly(bot: Bot) -> None:
    from services.weekly import build_weekly

    await notify.send_to_group(bot, "reports", await build_weekly(), parse_mode=None)


async def job_cleanup(bot: Bot) -> None:
    """Автоочистка: ТОЛЬКО messages и unanswered_questions старше 12 месяцев (решение Ивана 08.10)."""
    async with database.pool().acquire() as conn:
        async with conn.transaction():
            m = await conn.fetchval(f"WITH d AS (DELETE FROM messages WHERE created_at < now() - interval '{RETENTION}' "
                                    "RETURNING 1) SELECT count(*) FROM d")
            q = await conn.fetchval(f"WITH d AS (DELETE FROM unanswered_questions WHERE created_at < now() - "
                                    f"interval '{RETENTION}' RETURNING 1) SELECT count(*) FROM d")
    logger.info("Автоочистка: messages −%s, unanswered_questions −%s", m, q)
    await database.set_setting("cleanup:last", json.dumps({"at": shanghai_now().isoformat(), "messages": m,
                                                           "unanswered": q}))


JOBS = [
    Job("daily_report", time(9, 0), job_daily_report),
    Job("weekly", time(10, 0), job_weekly, weekday=0),
    Job("cleanup", time(10, 30), job_cleanup),
    Job("digest", time(19, 0), job_digest),
]


def due(job: Job, now: datetime, last_run: Optional[str]) -> bool:
    if job.weekday is not None and now.weekday() != job.weekday:
        return False
    scheduled = datetime.combine(now.date(), job.at, tzinfo=SHANGHAI_TZ)
    return scheduled <= now <= scheduled + MAX_LATE and last_run != now.date().isoformat()


async def tick(bot: Bot, now: Optional[datetime] = None) -> list[str]:
    now = now or shanghai_now()
    started = []
    for job in JOBS:
        key = f"job:{job.name}:last_run"
        if not due(job, now, await database.get_setting(key)):
            continue
        await database.set_setting(key, now.date().isoformat())  # до запуска: второй тик/процесс не повторит
        started.append(job.name)
        try:
            logger.info("Планировщик: запуск %s", job.name)
            await job.run(bot)
        except Exception as e:
            logger.exception("Планировщик: задача %s упала", job.name)
            await notify.notify_admin(bot, f"⚠️ Задача «{job.name}» упала: {type(e).__name__}: {e}")
    return started


async def run_scheduler(bot: Bot) -> None:
    logger.info("Планировщик запущен: %s", ", ".join(f"{j.name} {j.at:%H:%M}" for j in JOBS))
    while True:
        try:
            await tick(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Планировщик: ошибка тика")
        await asyncio.sleep(TICK_SECONDS)
