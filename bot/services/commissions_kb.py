"""Справочник «Комиссии маркетплейсов» для базы знаний (doc_key = commissions): строки mp_commissions → markdown.
Используют scripts/load_commissions.py (CSV) и services/commission_sync.py (автообновление Э7)."""
from services.commissions import OTHER_BUTTON, flagged, fmt_date, fmt_pct

DATES = ("wb_date", "ozon_rf_date", "ozon_cn_date")


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
        "источник_фактов: таблица mp_commissions (источник и дата — у каждой строки)",
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
        "Средние ставки комиссии по категории для продавцов из РФ и из Китая (схема и источник указаны у каждой цифры). "
        "Это ориентир: у отдельных товаров внутри категории ставка может отличаться.",
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
