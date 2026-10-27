"""Комиссии маркетплейсов (ручная загрузка; автообновление — services/commission_sync.py): CSV → таблица mp_commissions → справочник data/kb/Комиссии_маркетплейсов.md → база знаний.

Запуск (из /opt/consultinchina-bot, CSV положить в data/):
  docker compose run --rm --no-deps bot python scripts/load_commissions.py /app/data/mp_commissions.csv [--no-ingest]
Таблица заменяется целиком в одной транзакции (колонки Э7 wb_fbw_pct, ozon_rf_detail,
ozon_cn_tiers при этом обнуляются — их заполняет автообновление). Справочник загружается через тот же механизм, что ingest_kb.py
(doc_key = commissions); тот же текст — «без изменений».
"""
import asyncio
import csv
import sys
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, "/app")

import database
from services.commissions_kb import DATES, build_markdown
from services.kb_ingest import KB_DIR, KB_SOURCES, ingest

PCT = ("wb_rf_pct", "wb_cn_pct", "ozon_rf_pct", "ozon_cn_pct")


def parse_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = set(database.COMMISSION_COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"В CSV нет колонок: {', '.join(sorted(missing))}")
        rows = []
        for i, raw in enumerate(reader, start=1):
            r = {k: (raw.get(k) or "").strip() or None for k in database.COMMISSION_COLUMNS}
            if not r["button"]:
                raise ValueError(f"Строка {i}: пустая колонка button")
            for k in PCT:
                if r[k] is not None:
                    try:
                        r[k] = Decimal(r[k].replace(",", "."))
                    except InvalidOperation:
                        raise ValueError(f"Строка {i} ({r['button']}): {k} = «{r[k]}» — не число")
            for k in DATES:
                if r[k] is not None:
                    r[k] = date.fromisoformat(r[k])
            r["position"] = i
            rows.append(r)
    buttons = [r["button"] for r in rows]
    if len(set(buttons)) != len(buttons):
        raise ValueError("В CSV повторяются значения button")
    return rows


async def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    rows = parse_csv(Path(args[0]))
    md = build_markdown(rows)
    filename = KB_SOURCES["commissions"][0]
    (KB_DIR / filename).write_text(md, encoding="utf-8")
    print(f"CSV: {len(rows)} строк; справочник {filename}: {len(md)} симв.")

    await database.init_pool()
    try:
        await database.replace_commissions(rows)
        print(f"mp_commissions: заменено, строк {len(rows)}")
        if "--no-ingest" in sys.argv:
            return 0
        r = await ingest("commissions", filename, KB_SOURCES["commissions"][1], md.encode("utf-8"))
        if r.status == "unchanged":
            print("[без изменений] commissions")
        else:
            print(f"[{'заменён' if r.replaced else 'загружен'}] commissions: {r.chunks} фрагм., {r.tokens} токенов")
    finally:
        await database.close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
