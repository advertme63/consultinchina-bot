"""Ручная проверка Э4 на ТЕСТОВОЙ выборке (source='test' OR telegram_id<0). В группы ничего не отправляет,
settings планировщика не трогает, «новые вопросы» отправленными не помечает.

Запуск (из /opt/consultinchina-bot):
  docker compose run --rm --no-deps -v /root:/report bot python scripts/check_e4.py [день ДД.ММ.ГГГГ]
Вывод — в stdout и /report/e4_check.md (на хосте /root/e4_check.md), CSV — /report/e4_export_test.csv.
"""
import asyncio
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, "/app")

import database
from services.digest import build_digest
from services.export import build_csv
from services.limits import SHANGHAI_TZ, shanghai_today
from services.scheduler import JOBS, due
from services.stats import daily_report, period_report
from services.weekly import build_weekly

OUT = Path("/report")


def scheduler_checks() -> list[str]:
    tz = SHANGHAI_TZ
    j = {x.name: x for x in JOBS}
    cases = [
        ("daily_report", datetime(2026, 10, 9, 8, 59, tzinfo=tz), None, False),
        ("daily_report", datetime(2026, 10, 9, 9, 0, tzinfo=tz), None, True),
        ("daily_report", datetime(2026, 10, 9, 9, 5, tzinfo=tz), "2026-10-09", False),
        ("daily_report", datetime(2026, 10, 9, 12, 1, tzinfo=tz), None, False),
        ("weekly", datetime(2026, 10, 12, 10, 0, tzinfo=tz), None, True),
        ("weekly", datetime(2026, 10, 13, 10, 0, tzinfo=tz), None, False),
        ("cleanup", datetime(2026, 10, 9, 10, 29, tzinfo=tz), None, False),
        ("cleanup", datetime(2026, 10, 9, 10, 30, tzinfo=tz), "2026-10-08", True),
        ("digest", datetime(2026, 10, 9, 19, 30, tzinfo=tz), None, True),
    ]
    out = []
    for name, now, last, exp in cases:
        got = due(j[name], now, last)
        out.append(f"{'OK  ' if got == exp else 'FAIL'} {name:12} {now:%a %d.%m %H:%M} last_run={last} → {got}")
    return out


async def main() -> None:
    day = datetime.strptime(sys.argv[1], "%d.%m.%Y").date() if len(sys.argv) > 1 else shanghai_today()
    await database.init_pool()
    parts = [f"# Проверка Э4 на тестовой выборке · {datetime.now():%d.%m.%Y %H:%M}\n"]
    try:
        parts += ["## Планировщик: правило запуска\n", "```", *scheduler_checks(), "```\n"]
        parts += [f"## Отчёт 09:00 (за {day:%d.%m.%Y}, scope=test)\n", "```", await daily_report(day, scope="test"), "```\n"]
        parts += ["## Пустой день (01.01.2026, scope=test)\n", "```", await daily_report(date(2026, 1, 1), scope="test"), "```\n"]
        parts += ["## /stats 7 (scope=test)\n", "```", await period_report(7, scope="test"), "```\n"]
        digest = await build_digest(scope="test", mark=False)
        parts += ["## /digest — сводка новых вопросов (scope=test, без пометки)\n", "```", digest or "(новых вопросов нет — не отправляется)", "```\n"]
        parts += [f"## /report — анализ недели (scope=test, неделя по {day:%d.%m.%Y})\n", "```", await build_weekly(scope="test", end_day=day), "```\n"]
        data, n = await build_csv(7, scope="test")
        (OUT / "e4_export_test.csv").write_bytes(data)
        head = data.decode("utf-8-sig").splitlines()[:3]
        parts += [f"## /export 7 (scope=test): {n} строк, {len(data)} байт → /root/e4_export_test.csv\n", "```", *head, "```\n"]
    finally:
        await database.close_pool()
    text = "\n".join(parts)
    (OUT / "e4_check.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    asyncio.run(main())
