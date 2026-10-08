"""Сводка новых вопросов (ТЗ 7.1): 19:00 по Шанхаю → «CinC Лиды». Вопросов нет — не отправляем.
Похожие вопросы группирует Claude; после отправки строки помечаются in_digest = true."""
import logging
from typing import Optional

import database
from services.claude_client import ask_claude_text
from services.stats import scope_sql

logger = logging.getLogger(__name__)
MAX_QUESTIONS = 80


async def pending(scope: str = "real") -> list:
    async with database.pool().acquire() as conn:
        return await conn.fetch(f"""
            SELECT q.id, q.question, q.created_at, COALESCE(q.segment, u.segment) seg
            FROM unanswered_questions q JOIN users u USING (telegram_id)
            WHERE q.in_digest = false AND {scope_sql('u', scope)}
            ORDER BY q.created_at LIMIT {MAX_QUESTIONS}""")


async def build_digest(scope: str = "real", mark: bool = True) -> Optional[str]:
    """Текст сводки или None, если новых вопросов нет. mark — пометить вошедшие как отправленные."""
    rows = await pending(scope)
    if not rows:
        return None
    listing = "\n".join(f"- {r['question'][:300]}" for r in rows)
    prompt = (
        "Ниже — вопросы клиентов, на которые бот Consult in China не нашёл ответа в справочниках. "
        "Сгруппируй похожие по смыслу. Для каждой группы: короткое название темы, сколько вопросов, "
        "1–2 примера формулировки. Затем одной строкой — в какой справочник логичнее дописать ответы. "
        "Без вступлений, без разметки Markdown, списком с «•».\n\n" + listing
    )
    try:
        grouped, _, _ = await ask_claude_text(prompt, max_tokens=700, purpose="digest")
    except Exception:
        logger.exception("digest: группировка Claude не удалась — отправляю списком")
        grouped = "\n".join(f"• {r['question'][:200]}" for r in rows)
    text = f"🆕 Новые вопросы без ответа из базы: {len(rows)}\n\n{grouped}"
    if mark:
        async with database.pool().acquire() as conn:
            await conn.execute("UPDATE unanswered_questions SET in_digest = true WHERE id = ANY($1::int[])",
                               [r["id"] for r in rows])
    return text
