"""Прогон 25 тестовых вопросов Э2 (docs/tests_e2.md) через тот же код ответа, что у бота, без Telegram.

Запуск (из /opt/consultinchina-bot):
  docker compose run --rm --no-deps -v ./docs:/app/docs:ro -v /root:/report bot python scripts/run_tests.py [номера...]
Отчёт: /report/e2_test_report.md (на хосте /root/e2_test_report.md).

Тестовые пользователи: telegram_id = -1000 - номер теста, source = 'test'. Лимит на них не действует
(лимит проверяется в хендлере Telegram, а здесь вызывается только ядро). Перед прогоном их прошлые
сообщения, «новые вопросы» и события удаляются, чтобы история не перетекала между прогонами.
"""
import asyncio
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "/app")

import database
from config import config
from services.answer import answer_question

TESTS_FILE = Path("/app/docs/tests_e2.md")
REPORT = Path(os.environ.get("E2_REPORT", "/report/e2_test_report.md"))
VOYAGE_PAUSE = 21  # Voyage без карты: 3 запроса в минуту

# Оценка стоимости, $ за 1 млн токенов (уровень Sonnet): вход / запись в кэш / чтение кэша / выход
PRICE_IN, PRICE_CACHE_WRITE, PRICE_CACHE_READ, PRICE_OUT = 3.0, 3.75, 0.30, 15.0


def load_tests() -> list[dict]:
    tests = []
    for line in TESTS_FILE.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5 or not cells[0].isdigit():
            continue
        question = cells[1]
        if "→" in question:  # диалог: «первое» → затем «второе»
            turns = re.findall(r"«([^»]+)»", question)
        else:
            turns = [question.strip("«»")]
        tests.append({"n": int(cells[0]), "turns": turns, "must": cells[2], "never": cells[3], "expected": cells[4]})
    return tests


def test_user_id(n: int) -> int:
    return -1000 - n


async def prepare_users(numbers: list[int]) -> None:
    ids = [test_user_id(n) for n in numbers]
    async with database.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM messages WHERE telegram_id = ANY($1::bigint[])", ids)
            await conn.execute("DELETE FROM unanswered_questions WHERE telegram_id = ANY($1::bigint[])", ids)
            await conn.execute("DELETE FROM events WHERE telegram_id = ANY($1::bigint[])", ids)
            await conn.executemany(
                """
                INSERT INTO users (telegram_id, username, first_name, source)
                VALUES ($1, 'e2_test', 'Тест', 'test')
                ON CONFLICT (telegram_id) DO UPDATE SET source = 'test'
                """,
                [(i,) for i in ids],
            )


def fmt_frag(r) -> str:
    dist = f"{r['distance']:.3f}" if r["distance"] is not None else "—"
    ranks = f"вектор #{r['vrank'] or '—'}, текст #{r['frank'] or '—'} (слов {r['matched']}/{r['n_lex']})"
    path = r["content"].split("\n", 1)[0]
    return f"`{r['doc_key']}` {dist} · {ranks} · {path}"


async def main() -> None:
    only = {int(a) for a in sys.argv[1:] if a.isdigit()}
    tests = [t for t in load_tests() if not only or t["n"] in only]
    await database.init_pool()
    totals = dict(tin=0, tout=0, cread=0, cwrite=0, calls=0)
    out = []
    started = time.monotonic()
    try:
        await prepare_users([t["n"] for t in tests])
        first = True
        for t in tests:
            uid = test_user_id(t["n"])
            for i, q in enumerate(t["turns"]):
                if not first:
                    await asyncio.sleep(VOYAGE_PAUSE)
                first = False
                print(f"[{t['n']}.{i + 1}] {q}", flush=True)
                try:
                    r = await answer_question(uid, q)
                except Exception as e:
                    out.append(f"## {t['n']}. {q}\n\n**ОШИБКА:** {type(e).__name__}: {e}\n")
                    print(f"    ОШИБКА {type(e).__name__}: {e}", flush=True)
                    continue
                totals["tin"] += r.tokens_in
                totals["tout"] += r.tokens_out
                totals["cread"] += r.cache_read
                totals["cwrite"] += r.cache_write
                totals["calls"] += 1
                label = f"{t['n']}" + (f".{i + 1}" if len(t["turns"]) > 1 else "")
                best = f"{r.best_distance:.3f}" if r.best_distance is not None else "—"
                block = [
                    f"## {label}. {q}",
                    "",
                    f"**Ожидается (kb / cta):** {t['expected']} · **Должно быть:** {t['must']} · **Нельзя:** {t['never']}",
                    "",
                    f"**Получено:** answered_from_kb = `{str(r.answered_from_kb).lower()}` · intent = `{r.intent}` · "
                    f"cta = `{r.cta}` · релевантно = `{str(r.relevant).lower()}` · лучший вектор = {best} · "
                    f"токены {r.tokens_in}/{r.tokens_out} · {r.latency_ms} мс",
                    "",
                    "**Топ-3 поиска:**",
                    *[f"{k + 1}. {fmt_frag(f)}" for k, f in enumerate(r.fragments[:3])],
                    "",
                    "**Ответ:**",
                    "",
                    *[f"> {line}" if line else ">" for line in r.answer.splitlines()],
                    "",
                ]
                out.append("\n".join(block))
                print(f"    kb={r.answered_from_kb} intent={r.intent} cta={r.cta} best={best} "
                      f"top3={[f['doc_key'] for f in r.fragments[:3]]}", flush=True)

        async with database.pool().acquire() as conn:
            unanswered = await conn.fetch(
                "SELECT telegram_id, question FROM unanswered_questions WHERE telegram_id = ANY($1::bigint[]) ORDER BY id",
                [test_user_id(t["n"]) for t in tests],
            )
    finally:
        await database.close_pool()

    fresh_in = totals["tin"] - totals["cread"] - totals["cwrite"]
    cost = (fresh_in * PRICE_IN + totals["cwrite"] * PRICE_CACHE_WRITE + totals["cread"] * PRICE_CACHE_READ
            + totals["tout"] * PRICE_OUT) / 1_000_000
    head = [
        "# Отчёт о прогоне тестов Э2",
        "",
        f"- Дата: {datetime.now():%d.%m.%Y %H:%M} (время сервера) · модель `{config.CLAUDE_MODEL}` · "
        f"порог {config.RELEVANCE_THRESHOLD}",
        f"- Вызовов Claude: {totals['calls']} · длительность {int(time.monotonic() - started)} с",
        f"- Токены: вход {totals['tin']} (из них чтение кэша {totals['cread']}, запись в кэш {totals['cwrite']}), "
        f"выход {totals['tout']}",
        f"- Оценка стоимости по ценам уровня Sonnet (${PRICE_IN}/${PRICE_OUT} за 1 млн): ~${cost:.2f}",
        f"- В unanswered_questions записано: {len(unanswered)} — "
        + ("; ".join(f"тест {-1000 - u['telegram_id']}: «{u['question'][:60]}»" for u in unanswered) or "нет"),
        "",
        "---",
        "",
    ]
    REPORT.write_text("\n".join(head) + "\n".join(out), encoding="utf-8")
    print(f"\nОтчёт: /root/{REPORT.name} · вызовов {totals['calls']} · токены вход {totals['tin']} "
          f"(кэш чтение {totals['cread']}, запись {totals['cwrite']}), выход {totals['tout']} · ~${cost:.2f}")


if __name__ == "__main__":
    asyncio.run(main())
