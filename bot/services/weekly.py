"""Еженедельный анализ диалогов (ТЗ 7.3): пн 10:00 по Шанхаю → «CinC Отчёты».
Вход для Claude ограничен (MAX_DIALOGS, обрезка ответов), чтобы расход не рос вместе с трафиком."""
import logging
from datetime import timedelta

import database
from config import config
from services.claude_client import ask_claude_text
from services.limits import shanghai_today
from services.stats import collect, day_bounds, render, scope_sql

logger = logging.getLogger(__name__)
MAX_DIALOGS = 150


async def build_weekly(scope: str = "real", end_day=None) -> str:
    """По умолчанию — 7 полных суток до вчера включительно (запуск в пн 10:00 → пн–вс прошлой недели)."""
    end_day = end_day or (shanghai_today() - timedelta(days=1))
    first = end_day - timedelta(days=6)
    start, end = day_bounds(first, 7)
    period = f"{first:%d.%m}–{end_day:%d.%m.%Y}"
    stats = await collect(start, end, f"Неделя {period}", scope)
    u = scope_sql("u", scope)
    async with database.pool().acquire() as conn:
        dialogs = await conn.fetch(f"""
            SELECT m.question, m.answer, m.answered_from_kb, m.best_distance, m.rating, m.feedback,
                   COALESCE(m.segment, 'none') seg
            FROM messages m JOIN users u USING (telegram_id)
            WHERE m.created_at >= $1 AND m.created_at < $2 AND {u}
            ORDER BY m.created_at DESC LIMIT {MAX_DIALOGS}""", start, end)
    if not dialogs and stats.is_empty:
        return f"📈 Анализ недели {period} — диалогов не было."

    def row(d) -> str:
        flags = []
        if not d["answered_from_kb"]:
            flags.append("НЕТ В БАЗЕ")
        if d["best_distance"] is not None and d["best_distance"] > config.RELEVANCE_THRESHOLD:
            flags.append("слабая релевантность")
        if d["rating"] == -1:
            flags.append("👎" + (f" «{d['feedback'][:200]}»" if d["feedback"] else ""))
        return (f"[{d['seg']}{'; ' + '; '.join(flags) if flags else ''}] В: {d['question'][:200]}"
                f" | О: {(d['answer'] or '')[:300]}")

    prompt = (
        f"Проанализируй диалоги клиентов с ботом Consult in China за неделю {period}. Цифры воронки и сегментов "
        "ниже уже посчитаны — используй их как есть, не пересчитывай.\n\n"
        f"{render(stats)}\n\nДиалоги (последние {len(dialogs)}):\n" + "\n".join(row(d) for d in dialogs)
        + "\n\nНапиши отчёт для владельца (без Markdown-заголовков, разделы с эмодзи, списки «•»):\n"
        "1. Топ-10 тем, о которых спрашивали (с числом вопросов).\n"
        "2. Пробелы в базе: вопросы «НЕТ В БАЗЕ», сгруппированные по смыслу, — в какой справочник что дописать.\n"
        "3. Слабые ответы: все 👎 с комментариями и ответы со слабой релевантностью — что исправить в промпте "
        "или справочнике.\n"
        "4. Воронка по сегментам: пользователи → вопросы → квалификация → заявка/заказ → взято в работу.\n"
        "5. Темы для контента: 5 вопросов, которые стоит превратить в пост или статью.\n"
        "Только по данным выше, без выдумок. Сжато: весь отчёт — до ~700 слов."
    )
    text, _, _ = await ask_claude_text(prompt, max_tokens=4000, purpose="weekly")
    return f"📈 Анализ недели {period}\n\n{text}"
