"""Комиссии маркетплейсов: CSV → таблица mp_commissions → справочник data/kb/Комиссии_маркетплейсов.md → база знаний.

Запуск (из /opt/consultinchina-bot, CSV положить в data/):
  docker compose run --rm --no-deps bot python scripts/load_commissions.py /app/data/mp_commissions.csv [--no-ingest]
Таблица заменяется целиком в одной транзакции. Справочник загружается через тот же механизм, что ingest_kb.py
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
from services.commissions import OTHER_BUTTON, flagged, fmt_date, fmt_pct
from services.kb_ingest import KB_DIR, KB_SOURCES, ingest

PCT = ("wb_rf_pct", "wb_cn_pct", "ozon_rf_pct", "ozon_cn_pct")
DATES = ("wb_date", "ozon_rf_date", "ozon_cn_date")


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


def _mp_line(r: dict, mp: str) -> str:
    title = "WB" if mp == "wb" else "Ozon"
    cat = r[f"{mp}_category"]
    rf, cn = r[f"{mp}_rf_pct"], r[f"{mp}_cn_pct"]
    if not cat or rf is None or cn is None:
        return f"- {title}: средней ставки в справочнике нет — ставку не называем, проверим на разборе."
    if flagged(r["note"], mp):
        return f"- {title} («{cat}»): данные требуют уточнения — ставку не называем, проверим на разборе."
    if mp == "wb":
        src = f"источник: {r['wb_source']}, данные на {fmt_date(r['wb_date'])}"
    else:
        src = (f"источники: из РФ — {r['ozon_rf_source']}, на {fmt_date(r['ozon_rf_date'])}; "
               f"из Китая — {r['ozon_cn_source']}, на {fmt_date(r['ozon_cn_date'])}")
    if mp == "ozon":
        return (f"- {title} («{cat}»): из РФ — около {fmt_pct(rf)}% (FBS), из Китая — около {fmt_pct(cn)}% "
                f"(rFBS: доставку до покупателя организует продавец) ({src}).")
    return f"- {title} («{cat}»): продавцы из РФ — около {fmt_pct(rf)}%, из Китая — около {fmt_pct(cn)}% ({src})."


def build_markdown(rows: list[dict]) -> str:
    # Версия — самая свежая дата в данных: тот же CSV даёт тот же текст и хеш (повторная загрузка — «без изменений»)
    version = max(r[k] for r in rows for k in DATES if r[k])
    out = [
        "---",
        "id: KB-COM-01",
        "doc_key: commissions",
        "title: Комиссии маркетплейсов WB и Ozon по категориям",
        "тема: комиссии маркетплейсов",
        "тип: справочник",
        f"версия: {version:%Y.%m.%d}",
        "источник_фактов: таблица mp_commissions (Я-Селлер, GetSeller, MarketBridge — даты по каждой строке)",
        "---",
        "",
        "# Комиссии маркетплейсов WB и Ozon по категориям",
        "",
        "## Как пользоваться (для бота)",
        "",
        "- Ставку комиссии называй только из этого справочника. Всегда с датой и источником.",
        "- Всегда добавляй: ставки часто меняются; WB объявил уравнивание ставок для продавцов из РФ и Китая — разница может сократиться.",
        "- Если данным по площадке больше 60 дней или ставка помечена «проверим на разборе» — цифру не называй, предложи разбор с нашим специалистом.",
        "- Спрашивают без категории — уточни категорию товара. Категории нет в справочнике — ставку не называй.",
        "- Разница в комиссии — не итоговая выгода: не учтены логистика, хранение, эквайринг, налоги и содержание компании в Китае.",
        "- По Ozon разницу между ставками РФ и Китая не считай: цифры из разных источников и разных схем (FBS и rFBS).",
        "",
        "Средние ставки комиссии для продавцов из РФ и из Китая (схема и источник указаны у каждой цифры). "
        "Это ориентир по данным агрегаторов, а не официальная таблица площадок.",
        "",
    ]
    for r in rows:
        if r["button"] == OTHER_BUTTON:
            continue
        out.append(f"## {r['button']}")
        out.append("")
        out.append(_mp_line(r, "wb"))
        out.append(_mp_line(r, "ozon"))
        out.append("")
    return "\n".join(out)


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
