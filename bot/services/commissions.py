"""Комиссии маркетплейсов (Э3.5, разделы 2 и 7): таблица mp_commissions и блок в итоге селлера.
Цифры в блок подставляет Python по шаблону — Claude их не генерирует."""
from datetime import date
from decimal import Decimal
from html import escape
from typing import Optional

import database
from config import config
from services.limits import shanghai_today

NO_DATA_PHRASE = "Ставку по вашей категории проверим на разборе."
OTHER_BUTTON = "Другое"
MARKETPLACES = {"WB": ("wb",), "Ozon": ("ozon",), "WB и Ozon": ("wb", "ozon"), "Пока не продаю": ()}


def fmt_pct(v) -> str:
    d = Decimal(v).normalize()
    s = format(d, "f")
    return s.replace(".", ",")


def fmt_date(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def flagged(note: Optional[str], mp: str) -> bool:
    """Пометки «*» / «уточнить» в примечании относятся к площадке, названной в той же части примечания."""
    name = "Ozon" if mp == "ozon" else "WB"
    for part in (note or "").split(";"):
        if name in part and ("*" in part or "уточн" in part.lower()):
            return True
    return False


def _is_stale(d: Optional[date], today: date) -> bool:
    return d is None or (today - d).days > config.COMMISSIONS_MAX_AGE_DAYS


def marketplace_data(row, mp: str, today: Optional[date] = None) -> Optional[dict]:
    """Данные площадки для показа или None (нет ставки, устарели, помечены «уточнить»)."""
    today = today or shanghai_today()
    if row is None or row["button"] == OTHER_BUTTON or flagged(row["note"], mp):
        return None
    if mp == "wb":
        rf, cn = row["wb_rf_pct"], row["wb_cn_pct"]
        dates = [row["wb_date"]]
        sources = f"{row['wb_source']}, данные на {fmt_date(row['wb_date'])}" if row["wb_date"] else ""
        category = row["wb_category"]
    else:
        rf, cn = row["ozon_rf_pct"], row["ozon_cn_pct"]
        dates = [row["ozon_rf_date"], row["ozon_cn_date"]]
        sources = (
            f"из РФ — {row['ozon_rf_source']}, на {fmt_date(row['ozon_rf_date'])}; "
            f"из Китая — {row['ozon_cn_source']}, на {fmt_date(row['ozon_cn_date'])}"
            if all(dates) else ""
        )
        category = row["ozon_category"]
    if rf is None or cn is None or not category or any(_is_stale(d, today) for d in dates):
        return None
    return {"rf": rf, "cn": cn, "sources": sources, "category": category}


OZON_CN_SCHEME = "rFBS: доставку до покупателя организует продавец"


def _line(mp_title: str, d: dict) -> str:
    if mp_title == "Ozon":
        # Ozon: ставки из разных источников и схем (FBS / rFBS) — разницу не считаем (решение Ивана 08.10)
        return (
            f"<b>Комиссии в категории «{escape(d['category'])}» на Ozon</b> ({escape(d['sources'])}):\n"
            f"из РФ — около {fmt_pct(d['rf'])}% (FBS), из Китая — около {fmt_pct(d['cn'])}% ({OZON_CN_SCHEME})."
        )
    diff = Decimal(d["rf"]) - Decimal(d["cn"])
    if diff > 0:
        tail = f"Разница — около {fmt_pct(diff)} п.п."
    else:
        tail = "Ставка для продавцов из Китая не ниже."
    return (
        f"<b>Комиссии в категории «{escape(d['category'])}» на {mp_title}</b> ({escape(d['sources'])}):\n"
        f"продавцы из РФ — около {fmt_pct(d['rf'])}%, из Китая — около {fmt_pct(d['cn'])}%. {tail}"
    )


async def seller_block(answers: dict, today: Optional[date] = None) -> str:
    """Блок комиссий для итога селлера (HTML). Пусто — блок не показываем («Пока не продаю»)."""
    mps = MARKETPLACES.get(answers.get("marketplace") or "", ())
    if not mps:
        return ""
    row = await database.get_commission(answers.get("category") or "")
    lines, missing = [], []
    for mp in mps:
        d = marketplace_data(row, mp, today)
        title = "WB" if mp == "wb" else "Ozon"
        if d:
            lines.append(_line(title, d))
        else:
            missing.append(title)
    if not lines:
        return NO_DATA_PHRASE
    parts = lines[:]
    if missing:
        parts.append(f"На {missing[0]} — {NO_DATA_PHRASE[0].lower()}{NO_DATA_PHRASE[1:]}")
    parts.append("Не учтены: логистика, хранение, эквайринг, налоги и содержание компании в Китае.")
    if "wb" in mps and "WB" not in missing:
        parts.append("WB объявил уравнивание ставок для продавцов из РФ и Китая — разница может сократиться.")
    return "\n".join(parts)


async def stale_warnings(today: Optional[date] = None) -> list[str]:
    """Для отчёта админу (Э4): какие данные старше порога."""
    today = today or shanghai_today()
    rows = await database.list_commissions()
    out = []
    for col, title in (("wb_date", "WB"), ("ozon_rf_date", "Ozon РФ"), ("ozon_cn_date", "Ozon Китай")):
        dates = [r[col] for r in rows if r[col]]
        if dates and _is_stale(min(dates), today):
            out.append(f"{title}: данные от {fmt_date(min(dates))} старше {config.COMMISSIONS_MAX_AGE_DAYS} дней")
    return out
