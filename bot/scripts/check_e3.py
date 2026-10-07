"""Проверка Э3 без Telegram-диалога.

  docker compose run --rm --no-deps bot python scripts/check_e3.py verdicts   — 4 набора ответов → вердикт и текст итога
  docker compose run --rm --no-deps bot python scripts/check_e3.py card       — одна карточка «ТЕСТ» в группу лидов

Тестовая карточка — от тестового пользователя -1020 (source='test'; его диалог — тест 20 Э2).
"""
import asyncio
import json
import sys

sys.path.insert(0, "/app")

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import database
from config import config
from services.leads import leads_chat_id, submit_lead
from services.qualify import VERDICT_LABELS, answers_line, verdict, verdict_text

SETS = {
    "подходит": {"marketplace": "WB и Ozon", "china_purchases": "Да, регулярно", "category": "посуда",
                 "turnover": "3–5 млн ₽", "tax_regime": "УСН 6%", "priority": "Платежи поставщикам"},
    "разбор": {"marketplace": "WB", "china_purchases": "Иногда", "category": "—",
               "turnover": "5–10 млн ₽", "tax_regime": "ООО на ОСН (с НДС)", "priority": "Маржа и налоги"},
    "рано": {"marketplace": "Ozon", "china_purchases": "Да, регулярно", "category": "игрушки",
             "turnover": "до 1 млн ₽", "tax_regime": "УСН 15%", "priority": "Платежи поставщикам"},
    "невыгодно": {"marketplace": "WB", "china_purchases": "Нет", "category": "одежда",
                  "turnover": "1–3 млн ₽", "tax_regime": "УСН 6%", "priority": "Маржа и налоги"},
}
TEST_UID = -1020


async def verdicts() -> None:
    total_in = total_out = 0
    for name, answers in SETS.items():
        v = verdict(answers)
        text, tin, tout = await verdict_text(answers, v)
        total_in += tin
        total_out += tout
        print(f"\n===== Набор «{name}» → вердикт: {v} ({VERDICT_LABELS[v]})")
        print(f"Ответы: {answers_line(answers)}")
        print(f"--- текст итога ({len(text.split())} слов):\n{text}")
    print(f"\nТокены Claude: вход {total_in}, выход {total_out}")


async def card() -> None:
    await database.init_pool()
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        answers = SETS["подходит"]
        await database.log_event(
            TEST_UID, "qualify_done", json.dumps({"verdict": verdict(answers), "answers": answers, "test": True}, ensure_ascii=False)
        )
        async with database.pool().acquire() as conn:
            await conn.execute("UPDATE users SET source = 'test' WHERE telegram_id = $1", TEST_UID)
            await conn.execute("DELETE FROM leads WHERE telegram_id = $1", TEST_UID)  # чтобы не сработал «повтор за 24 ч»
        user = await database.get_user(TEST_UID)
        print("Группа:", await leads_chat_id())
        lead_id = await submit_lead(bot, user, "Тест Тестович", "+7 000 000-00-00", test=True)
        lead = await database.get_lead(lead_id)
        print(f"Заявка #{lead_id}: status={lead['status']}, group_message_id={lead['group_message_id']}")
        print("Резюме:", lead["summary"])
    finally:
        await bot.session.close()
        await database.close_pool()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "verdicts"
    asyncio.run(verdicts() if mode == "verdicts" else card())
