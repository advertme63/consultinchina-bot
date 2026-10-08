"""/export <дней> (ТЗ 7.3): CSV диалогов для разбора. UTF-8 с BOM и «;» — открывается в Excel.
Вместо telegram_id — внутренний номер пользователя (users.export_no). Без тестовых и админа."""
import csv
import io
from datetime import timedelta

import database
from services.limits import SHANGHAI_TZ, shanghai_today
from services.stats import day_bounds, scope_sql

HEADER = ["дата (Шанхай)", "№ пользователя", "источник", "сегмент", "вопрос", "ответ", "документы", "оценка", "комментарий"]


async def build_csv(days: int, scope: str = "real") -> tuple[bytes, int]:
    first = shanghai_today() - timedelta(days=days - 1)
    start, end = day_bounds(first, days)
    async with database.pool().acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT m.created_at, u.export_no, u.source, COALESCE(m.segment, u.segment) seg, m.question, m.answer,
                   m.doc_keys, m.rating, m.feedback
            FROM messages m JOIN users u USING (telegram_id)
            WHERE m.created_at >= $1 AND m.created_at < $2 AND {scope_sql('u', scope)}
            ORDER BY m.created_at""", start, end)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    w.writerow(HEADER)
    for r in rows:
        w.writerow([
            r["created_at"].astimezone(SHANGHAI_TZ).strftime("%d.%m.%Y %H:%M"), r["export_no"], r["source"] or "",
            r["seg"] or "", r["question"], r["answer"] or "", ", ".join(r["doc_keys"] or []),
            {1: "👍", -1: "👎"}.get(r["rating"], ""), r["feedback"] or "",
        ])
    return ("﻿" + buf.getvalue()).encode("utf-8"), len(rows)
