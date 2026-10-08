"""Проверка Э3.5 без Telegram-диалога.

  docker compose run --rm --no-deps bot python scripts/check_e35.py blocks    — блок комиссий по всем категориям (без API)
  docker compose run --rm --no-deps bot python scripts/check_e35.py verdicts  — 13 вердиктов: текст итога + блок Python
  docker compose run --rm --no-deps bot python scripts/check_e35.py cards     — 4 карточки «ТЕСТ» в группу, по сегменту

Тестовые пользователи: -1201…-1204, source = 'test'.
"""
import asyncio
import json
import re
import sys
from datetime import date

sys.path.insert(0, "/app")

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import database
from config import config
from services.commissions import seller_block
from services.leads import submit_lead
from services.segments import SEGMENTS, answers_line

SCENARIOS = [
    ("seller", {"marketplace": "WB и Ozon", "china_purchases": "Да, регулярно", "category": "Одежда",
                "turnover": "3–5 млн ₽", "tax_regime": "УСН 6%", "priority": "Платежи поставщикам"}),
    ("seller", {"marketplace": "WB", "china_purchases": "Иногда", "category": "Посуда",
                "turnover": "5–10 млн ₽", "tax_regime": "ООО на ОСН (с НДС)", "priority": "Маржа и налоги"}),
    ("seller", {"marketplace": "Ozon", "china_purchases": "Да, регулярно", "category": "Игрушки",
                "turnover": "до 1 млн ₽", "tax_regime": "УСН 15%", "priority": "Платежи поставщикам"}),
    ("seller", {"marketplace": "Пока не продаю", "china_purchases": "Нет", "category": "Другое",
                "turnover": "1–3 млн ₽", "tax_regime": "УСН 6%", "priority": "Маржа и налоги"}),
    ("importer", {"goods": "запчасти для станков", "frequency": "Разово", "volume": "до 1 млн ₽",
                  "payment": "Через агента", "pain": "Проверка поставщика", "ru_entity": "Да"}),
    ("importer", {"goods": "текстиль", "frequency": "Регулярно", "volume": "5–20 млн ₽",
                  "payment": "Платёжный агент", "pain": "Оплата", "ru_entity": "Да"}),
    ("importer", {"goods": "—", "frequency": "Регулярно", "volume": "1–5 млн ₽",
                  "payment": "Картой", "pain": "Логистика и таможня", "ru_entity": "Нет"}),
    ("expansion", {"activity": "Производство", "goal": "Продажи в Китае", "stage": "Работающий бизнес со своим продуктом",
                   "turnover": "50–300 млн ₽", "timing": "до года", "meetings": "Да"}),
    ("expansion", {"activity": "IT", "goal": "Представительство", "stage": "Идея",
                   "turnover": "Не скажу", "timing": "Изучаю", "meetings": "Нет"}),
    ("service", {"form": "WFOE", "need": "Налоги", "operations": "до 30", "reporting": "Есть долги", "city": "Шанхай"}),
    ("service", {"form": "WFOE", "need": "Сменить бухгалтера", "operations": "до 30", "reporting": "Не уверен", "city": "Шэньчжэнь"}),
    ("service", {"form": "WFOE", "need": "Ликвидация", "operations": "до 12", "reporting": "Да", "city": "Гуанчжоу"}),
    ("service", {"form": "Представительство", "need": "Найм", "operations": "до 12", "reporting": "Да", "city": "—"}),
]
CARD_SCENARIOS = {"seller": 0, "importer": 5, "expansion": 7, "service": 10}


def plain(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html)


async def blocks() -> None:
    rows = await database.list_commissions()
    print(f"mp_commissions: {len(rows)} строк")
    for r in rows:
        b = await seller_block({"marketplace": "WB и Ozon", "category": r["button"]})
        first = plain(b).split("\n")
        print(f"\n— {r['button']}:\n  " + "\n  ".join(first))
    print("\n=== «Пока не продаю» →", repr(await seller_block({"marketplace": "Пока не продаю", "category": "Одежда"})))
    stale = await seller_block({"marketplace": "WB и Ozon", "category": "Одежда"}, today=date(2026, 11, 5))
    print("=== Одежда, если сегодня 05.11.2026 (данные старше 60 дней) →", repr(plain(stale)))


async def verdicts() -> None:
    from handlers.qualify import verdict_message

    only = {int(a) for a in sys.argv[2:] if a.isdigit()}
    for n, (segment, answers) in enumerate(SCENARIOS, start=1):
        if only and n not in only:
            continue
        seg = SEGMENTS[segment]
        v = seg.verdict(answers)
        _, text = await verdict_message(segment, answers, v)
        print(f"\n===== {n}. {seg.label} → {v} «{seg.labels[v]}»")
        print(f"Ответы: {answers_line(answers, segment)}")
        print(f"--- итог:\n{text}")


async def cards() -> None:
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        for k, (segment, idx) in enumerate(CARD_SCENARIOS.items()):
            uid = -1201 - k
            seg_answers = SCENARIOS[idx][1]
            v = SEGMENTS[segment].verdict(seg_answers)
            async with database.pool().acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO users (telegram_id, username, first_name, source, segment)
                    VALUES ($1, 'e35_test', 'Тест', 'test', $2)
                    ON CONFLICT (telegram_id) DO UPDATE SET source = 'test', segment = EXCLUDED.segment
                    """,
                    uid,
                    segment,
                )
                await conn.execute("DELETE FROM leads WHERE telegram_id = $1", uid)
            await database.log_event(
                uid, "qualify_done",
                json.dumps({"segment": segment, "verdict": v, "answers": seg_answers, "test": True}, ensure_ascii=False),
            )
            user = await database.get_user(uid)
            lead_id = await submit_lead(bot, user, f"Тест {SEGMENTS[segment].label}", None, test=True)
            lead = await database.get_lead(lead_id)
            print(f"{SEGMENTS[segment].label}: заявка #{lead_id}, segment={lead['segment']}, verdict={lead['verdict']}, "
                  f"group_message_id={lead['group_message_id']}")
    finally:
        await bot.session.close()


async def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "blocks"
    await database.init_pool()
    try:
        await {"blocks": blocks, "verdicts": verdicts, "cards": cards}[mode]()
    finally:
        await database.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
