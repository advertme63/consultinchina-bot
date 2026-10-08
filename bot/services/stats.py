"""Статистика бота (ТЗ 7.2 + Э4): отчёт за сутки (09:00), /stats 7 | 30.

Сутки — по Шанхаю (UTC+8). Тестовые (source = 'test' OR telegram_id < 0) и админ исключаются из всех цифр,
кроме расхода Claude: он считается по ВСЕМ вызовам с разбивкой «из них тесты / админ».
scope='test' — те же цифры только по тестовым пользователям (ручная проверка до выкладки)."""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

import database
from config import config
from services.catalog import service_title
from services.limits import SHANGHAI_TZ, shanghai_today
from services.segments import SEGMENTS, VERDICT_LABELS

NO_SEGMENT = "none"
SEGMENT_ORDER = ["seller", "importer", "expansion", "service", "other", NO_SEGMENT]


def scope_sql(alias: str, scope: str) -> str:
    if scope == "test":
        return f"({alias}.source = 'test' OR {alias}.telegram_id < 0)"
    return (f"({alias}.telegram_id > 0 AND {alias}.source IS DISTINCT FROM 'test' "
            f"AND {alias}.telegram_id <> {int(config.ADMIN_TELEGRAM_ID)})")


def day_bounds(day: date, days: int = 1) -> tuple[datetime, datetime]:
    """[начало первого дня; начало дня после последнего) по Шанхаю."""
    start = datetime(day.year, day.month, day.day, tzinfo=SHANGHAI_TZ)
    return start, start + timedelta(days=days)


def seg_label(key: str) -> str:
    return SEGMENTS[key].label if key in SEGMENTS else "Без сегмента"


@dataclass
class Stats:
    title: str
    users: dict = field(default_factory=dict)  # сегмент → число
    new_by_source: dict = field(default_factory=dict)
    questions: dict = field(default_factory=dict)
    unanswered: dict = field(default_factory=dict)
    up: int = 0
    down: int = 0
    sugg_shown: int = 0
    sugg_clicks: dict = field(default_factory=dict)
    quals: dict = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))  # сегмент → вердикт → n
    leads: dict = field(default_factory=dict)
    leads_taken: int = 0
    orders: dict = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))  # сегмент → услуга → n
    orders_taken: int = 0
    limit_hit: dict = field(default_factory=dict)
    cost_total: float = 0.0
    cost_tests: float = 0.0
    cost_admin: float = 0.0
    cost_system: float = 0.0  # вызовы без пользователя: сводка 19:00, анализ недели
    warnings: list = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (sum(self.users.values()) or sum(self.questions.values()) or sum(self.leads.values())
                    or self.orders or self.quals)


async def collect(start: datetime, end: datetime, title: str, scope: str = "real") -> Stats:
    s = Stats(title=title)
    u = scope_sql("u", scope)
    w = "created_at >= $1 AND created_at < $2"  # для запросов к одной таблице

    def wa(alias: str) -> str:  # с таблицей — при JOIN с users (там тоже есть created_at)
        return f"{alias}.created_at >= $1 AND {alias}.created_at < $2"
    async with database.pool().acquire() as conn:
        for r in await conn.fetch(f"""
            WITH act AS (
                SELECT telegram_id FROM messages WHERE {w}
                UNION SELECT telegram_id FROM events WHERE {w} AND telegram_id IS NOT NULL
            )
            SELECT COALESCE(u.segment, 'none') seg, count(*) n FROM act JOIN users u USING (telegram_id)
            WHERE {u} GROUP BY 1""", start, end):
            s.users[r["seg"]] = r["n"]
        for r in await conn.fetch(f"""
            SELECT COALESCE(u.source, '—') src, count(*) n FROM users u
            WHERE u.created_at >= $1 AND u.created_at < $2 AND {u} GROUP BY 1 ORDER BY 2 DESC""", start, end):
            s.new_by_source[r["src"]] = r["n"]
        for r in await conn.fetch(f"""
            SELECT COALESCE(m.segment, 'none') seg, count(*) n,
                   count(*) FILTER (WHERE m.rating = 1) up, count(*) FILTER (WHERE m.rating = -1) down,
                   COALESCE(sum(cardinality(m.suggestions)), 0) shown
            FROM messages m JOIN users u USING (telegram_id) WHERE {wa("m")} AND {u} GROUP BY 1""", start, end):
            s.questions[r["seg"]] = r["n"]
            s.up += r["up"]
            s.down += r["down"]
            s.sugg_shown += r["shown"]
        for r in await conn.fetch(f"""
            SELECT COALESCE(q.segment, 'none') seg, count(*) n
            FROM unanswered_questions q JOIN users u USING (telegram_id) WHERE {wa("q")} AND {u} GROUP BY 1""", start, end):
            s.unanswered[r["seg"]] = r["n"]
        for r in await conn.fetch(f"""
            SELECT COALESCE(e.segment, 'none') seg, e.type, e.payload->>'verdict' verdict,
                   count(*) n, count(DISTINCT e.telegram_id) users
            FROM events e JOIN users u USING (telegram_id)
            WHERE {wa("e")} AND {u} AND e.type IN ('qualify_done', 'suggestion_click', 'limit_hit')
            GROUP BY 1, 2, 3""", start, end):
            if r["type"] == "qualify_done":
                s.quals[r["seg"]][r["verdict"] or "?"] += r["n"]
            elif r["type"] == "suggestion_click":
                s.sugg_clicks[r["seg"]] = s.sugg_clicks.get(r["seg"], 0) + r["n"]
            else:
                s.limit_hit[r["seg"]] = s.limit_hit.get(r["seg"], 0) + r["users"]
        for r in await conn.fetch(f"""
            SELECT COALESCE(l.segment, 'none') seg, l.service, count(*) n,
                   count(*) FILTER (WHERE l.status <> 'new') taken
            FROM leads l JOIN users u USING (telegram_id) WHERE {wa("l")} AND {u} GROUP BY 1, 2""", start, end):
            if r["service"]:
                s.orders[r["seg"]][r["service"]] += r["n"]
                s.orders_taken += r["taken"]
            else:
                s.leads[r["seg"]] = s.leads.get(r["seg"], 0) + r["n"]
                s.leads_taken += r["taken"]
        cost = await conn.fetchrow(f"""
            SELECT COALESCE(sum(l.cost_usd), 0) total,
                   COALESCE(sum(l.cost_usd) FILTER (WHERE l.telegram_id < 0 OR u.source = 'test'), 0) tests,
                   COALESCE(sum(l.cost_usd) FILTER (WHERE l.telegram_id = {int(config.ADMIN_TELEGRAM_ID)}), 0) admin,
                   COALESCE(sum(l.cost_usd) FILTER (WHERE l.telegram_id IS NULL), 0) system
            FROM llm_usage l LEFT JOIN users u USING (telegram_id) WHERE {wa("l")}""", start, end)
        s.cost_total, s.cost_tests, s.cost_admin = float(cost["total"]), float(cost["tests"]), float(cost["admin"])
        s.cost_system = float(cost["system"])
    return s


def _join(d: dict, fmt=lambda k: k) -> str:
    return ", ".join(f"{fmt(k)} {v}" for k, v in sorted(d.items(), key=lambda x: -x[1]))


def render(s: Stats) -> str:
    cost = f"~${s.cost_total:.2f}" + (
        f" (из них тесты ${s.cost_tests:.2f}, админ ${s.cost_admin:.2f}, служебные ${s.cost_system:.2f})"
        if s.cost_tests or s.cost_admin or s.cost_system else ""
    )
    if s.is_empty:
        line = f"📊 {s.title} — активности не было · расход Claude {cost}"
        return line + ("".join(f"\n⚠️ {w}" for w in s.warnings) if s.warnings else "")
    new_total = sum(s.new_by_source.values())
    quals_flat = defaultdict(int)
    for seg in s.quals.values():
        for v, n in seg.items():
            quals_flat[VERDICT_LABELS.get(v, v)] += n
    orders_flat = defaultdict(int)
    for seg in s.orders.values():
        for k, n in seg.items():
            orders_flat[service_title(k) or k] += n
    lines = [
        f"📊 {s.title}",
        f"Пользовались: {sum(s.users.values())}"
        + (f" (новых {new_total}: {_join(s.new_by_source)})" if new_total else " (новых 0)"),
        f"Вопросов: {sum(s.questions.values())} · без ответа из базы: {sum(s.unanswered.values())} · 👍 {s.up} / 👎 {s.down}",
        f"Нажатий на подсказки: {sum(s.sugg_clicks.values())} из {s.sugg_shown} показанных",
        f"Квалификаций: {sum(quals_flat.values())}" + (f" ({_join(quals_flat)})" if quals_flat else ""),
        f"Заявок: {sum(s.leads.values())} · взято в работу: {s.leads_taken}",
        f"Заказов через кнопку: {sum(orders_flat.values())}" + (f" ({_join(orders_flat)})" if orders_flat else "")
        + (f" · взято в работу: {s.orders_taken}" if orders_flat else ""),
        f"Упёрлись в лимит: {sum(s.limit_hit.values())}",
        f"Расход Claude API: {cost}",
        "",
        "По сегментам:",
    ]
    for seg in SEGMENT_ORDER:
        vals = (s.users.get(seg, 0), s.questions.get(seg, 0), sum(s.quals.get(seg, {}).values()),
                s.leads.get(seg, 0), sum(s.orders.get(seg, {}).values()), s.sugg_clicks.get(seg, 0))
        if any(vals):
            lines.append(f"{seg_label(seg)}: польз. {vals[0]} · вопр. {vals[1]} · квал. {vals[2]} · "
                         f"заявок {vals[3]} · заказов {vals[4]} · подсказок {vals[5]}")
    lines += [f"⚠️ {w}" for w in s.warnings]
    return "\n".join(lines)


async def daily_report(day: Optional[date] = None, scope: str = "real") -> str:
    """Отчёт за сутки (по умолчанию — вчера по Шанхаю) + предупреждения об устаревших комиссиях."""
    from services.commissions import stale_warnings

    day = day or (shanghai_today() - timedelta(days=1))
    start, end = day_bounds(day)
    s = await collect(start, end, f"Бот CinC · {day:%d.%m.%Y}", scope)
    s.warnings = [f"Комиссии: {w}" for w in await stale_warnings()]
    return render(s)


async def period_report(days: int = 7, scope: str = "real") -> str:
    """/stats [7|30]: последние N полных суток, включая сегодня."""
    end_day = shanghai_today()
    first = end_day - timedelta(days=days - 1)
    start, end = day_bounds(first, days)
    s = await collect(start, end, f"Бот CinC · {days} дн. ({first:%d.%m}–{end_day:%d.%m.%Y})", scope)
    return render(s)
