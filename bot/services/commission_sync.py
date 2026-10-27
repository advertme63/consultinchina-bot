"""Э7: автообновление комиссий WB (API) и Ozon (xlsx из базы знаний) в mp_commissions.

Площадки обновляются независимо: wb (РФ FBS/FBW + Китай), ozon_rf, ozon_cn.
Правила свёртки предметов в 24 категории бота (проверены на текущих данных, разведка 26–27.10):
  WB — простое среднее по всем предметам сопоставленных групп, округление до целого;
  Ozon РФ — самая частая ставка FBS «свыше 300 руб.» по типам товаров категории;
  Ozon Китай — медиана rFBS «1500,01–5000» по типам товаров (лист Full ChinaHK); в базе — все пороги rFBS и FBP.
Защита: не пишем площадку, если сопоставлено < 80% категорий, ставка изменилась > 15 п.п., ответ пустой/битый.
Старые данные остаются, в «CinC Отчёты» — тревога с причиной. Дата успешного обновления — settings."""
import json
import logging
import re
import statistics
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

import aiohttp
from aiogram import Bot

import database
from config import config
from services import notify
from services.commission_mapping import MAPPING
from services.commissions import fmt_date, fmt_pct
from services.limits import shanghai_today
from services.xlsx_lite import XlsxError, read_sheets, sheet_names

logger = logging.getLogger(__name__)

WB_URL = "https://common-api.wildberries.ru/api/v1/tariffs/commission?locale=ru"
WB_SOURCE = "WB API"
OZON_SOURCE = "Ozon, база знаний для продавцов"
OZON_RF_SHEET = "Прайс РФ (БЗ)"
OZON_CN_TREE, OZON_CN_FULL = "MP Tree Tarifs CN", "Full ChinaHK"
OZON_PAGES = (
    ("Ozon РФ", "https://seller-edu.ozon.ru/commissions-tariffs/commissions-tariffs-ozon/komissii-tovary-uslugi"),
    ("Ozon Китай", "https://global-help.ozon.com/commissions/ozon-fees/commissions"),
)
MIN_COVERAGE = 0.8
MAX_JUMP = Decimal(15)
WB_CACHE_SECONDS = 1800  # dry → запись без второго запроса (лимит WB — 1 запрос в окно)
OZON_REMIND_EVERY = 3  # дней между напоминаниями админу, пока файл Ozon не пришёл

TITLES = {"wb": "WB", "ozon_rf": "Ozon РФ", "ozon_cn": "Ozon Китай"}
# поля, которые бот показывает (по ним считаем «изменилось»)
SHOWN = {"wb": ("wb_rf_pct", "wb_cn_pct"), "ozon_rf": ("ozon_rf_pct",), "ozon_cn": ("ozon_cn_pct",)}
FIELD_TITLES = {"wb_rf_pct": "РФ", "wb_cn_pct": "Китай", "wb_fbw_pct": "FBW", "ozon_rf_pct": "РФ", "ozon_cn_pct": "Китай"}


class SyncError(Exception):
    """Данные площадки не получены или битые — старые остаются."""


class RateLimited(SyncError):
    pass


@dataclass
class Plan:
    platform: str
    data_date: date
    source: str
    values: dict = field(default_factory=dict)  # button → {колонка: значение}
    mapped: int = 0  # категорий, для которых площадка сопоставлена
    problems: list = field(default_factory=list)  # защита сработала — не пишем
    warnings: list = field(default_factory=list)
    changes: list = field(default_factory=list)  # (button, поле, было, стало)
    file_name: Optional[str] = None

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def changed_buttons(self) -> list[str]:
        return list(dict.fromkeys(b for b, f, _, _ in self.changes if f in SHOWN[self.platform]))


def _round(v: float) -> Decimal:
    return Decimal(str(v)).quantize(Decimal(1), rounding=ROUND_HALF_UP)


def _pct(v: float) -> Decimal:
    """Доля из xlsx (0.14000000000000001) → проценты с двумя знаками (14.00)."""
    return (Decimal(str(v)) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# --- WB --------------------------------------------------------------------------------

_wb_cache: dict[str, tuple[float, list]] = {}


async def fetch_wb(kind: str) -> list[dict]:
    """kind = RU | CN. Ответ 429 — RateLimited (не повторяем: ждём следующего запуска)."""
    cached = _wb_cache.get(kind)
    if cached and time.monotonic() - cached[0] < WB_CACHE_SECONDS:
        return cached[1]
    token = (getattr(config, f"WB_TARIFF_TOKEN_{kind}") or "").strip()
    if not token:
        raise SyncError(f"WB_TARIFF_TOKEN_{kind} не задан")
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.get(WB_URL, headers={"Authorization": token}) as r:
            if r.status == 429:
                raise RateLimited(f"WB ({kind}): 429 — лимит запросов, повтор при следующем запуске")
            if r.status != 200:
                raise SyncError(f"WB ({kind}): HTTP {r.status}")
            try:
                data = await r.json(content_type=None)
            except Exception as e:
                raise SyncError(f"WB ({kind}): ответ не JSON ({e})")
    report = data.get("report") if isinstance(data, dict) else None
    if not isinstance(report, list) or not report:
        raise SyncError(f"WB ({kind}): пустой ответ")
    _wb_cache[kind] = (time.monotonic(), report)
    return report


def plan_wb(ru: list[dict], cn: list[dict], today: date) -> Plan:
    plan = Plan("wb", today, WB_SOURCE)
    need_ru = ("parentID", "parentName", "subjectID", "kgvpMarketplace", "paidStorageKgvp")
    if not all(isinstance(x, dict) and all(k in x for k in need_ru) for x in ru):
        raise SyncError("WB (RU): битый ответ — нет полей kgvpMarketplace / paidStorageKgvp")
    if not all(isinstance(x, dict) and "kgvpChina" in x and "subjectID" in x for x in cn):
        raise SyncError("WB (CN): битый ответ — нет поля kgvpChina")
    china = {x["subjectID"]: x["kgvpChina"] for x in cn}
    by_parent: dict[int, list[dict]] = {}
    names: dict[int, str] = {}
    for x in ru:
        by_parent.setdefault(x["parentID"], []).append(x)
        names[x["parentID"]] = x["parentName"]
    for button, m in MAPPING.items():
        if not m["wb"]:
            continue
        plan.mapped += 1
        rows = []
        for pid, pname in m["wb"]:
            if pid not in by_parent:
                plan.warnings.append(f"WB: группа «{pname}» (parentID {pid}) пропала из дерева — категория «{button}»")
                continue
            if names[pid] != pname:
                plan.warnings.append(f"WB: группа {pid} переименована: «{pname}» → «{names[pid]}» (категория «{button}»)")
            rows += by_parent[pid]
        cn_rates = [china[x["subjectID"]] for x in rows if x["subjectID"] in china]
        if not rows or not cn_rates:
            continue
        plan.values[button] = {
            "wb_category": m["wb_category"],
            "wb_rf_pct": _round(statistics.mean(x["kgvpMarketplace"] for x in rows)),
            "wb_fbw_pct": _round(statistics.mean(x["paidStorageKgvp"] for x in rows)),
            "wb_cn_pct": _round(statistics.mean(cn_rates)),
            "wb_source": WB_SOURCE,
            "wb_date": today,
        }
    return plan


# --- Ozon ------------------------------------------------------------------------------

_DATE_RE = re.compile(r"(?<!\d)(\d{2})[_.\-]?(\d{2})[_.\-]?(20\d{2})(?!\d)")


def date_from_filename(name: str) -> Optional[date]:
    for d, m, y in _DATE_RE.findall(name or ""):
        try:
            return date(int(y), int(m), int(d))
        except ValueError:
            continue
    return None


def detect_ozon(data: bytes, file_name: str) -> str:
    """ozon_rf | ozon_cn по листам файла; «Селект», Fashion, WHD — не наш файл."""
    low = unicodedata.normalize("NFC", file_name or "").lower()
    if any(w in low for w in ("select", "selekt", "селект", "fashion", "фэшн", "whd")):
        raise SyncError(f"файл «{file_name}» — не тот (Селект / Fashion / WHD); нужен основной файл категорий")
    try:
        sheets = set(sheet_names(data))
    except XlsxError as e:
        raise SyncError(str(e))
    if OZON_RF_SHEET in sheets:
        return "ozon_rf"
    if OZON_CN_TREE in sheets and OZON_CN_FULL in sheets:
        return "ozon_cn"
    raise SyncError(f"в файле нет листа «{OZON_RF_SHEET}» (РФ) или «{OZON_CN_TREE}» + «{OZON_CN_FULL}» (Китай)")


def _num(s: str) -> Optional[float]:
    try:
        return float(s) if s not in ("", None) else None
    except ValueError:
        return None


def plan_ozon_rf(data: bytes, file_name: str, today: date) -> Plan:
    d = date_from_filename(file_name)
    if not d:
        raise SyncError(f"в имени файла «{file_name}» нет даты (ДДММГГГГ)")
    rows = read_sheets(data, [OZON_RF_SHEET]).get(OZON_RF_SHEET) or []
    if len(rows) < 3:
        raise SyncError("Ozon РФ: лист пустой")
    h0, h1 = rows[0], rows[1]
    groups, cur = [], ""
    for i in range(len(h1)):
        cur = h0[i] if i < len(h0) and h0[i].strip() else cur
        groups.append(cur.split("\n")[0].strip().upper())

    def col(group: str, head: Optional[str]) -> Optional[int]:
        for i, g in enumerate(groups):
            if g == group and (head is None or head in h1[i]):
                return i
        return None

    fbs, fbo, rfbs = col("FBS", "свыше 300"), col("FBO", "свыше 300"), col("RFBS", None)
    if fbs is None:
        raise SyncError("Ozon РФ: не найдена колонка FBS «свыше 300 руб.» — структура файла изменилась")
    items = [r for r in rows[2:] if len(r) > fbs and _num(r[fbs]) is not None]
    plan = Plan("ozon_rf", d, OZON_SOURCE, file_name=file_name)
    mains = {r[0] for r in items}
    for button, m in MAPPING.items():
        spec = m["ozon_rf"]
        if not spec:
            continue
        plan.mapped += 1
        sel = [r for r in items if r[0] == spec["main"] and ("category" not in spec or r[1] == spec["category"])]
        if not sel:
            where = spec["main"] + (f" / {spec['category']}" if "category" in spec else "")
            plan.warnings.append(f"Ozon РФ: нет категории «{where}» в файле — «{button}»"
                                 + ("" if spec["main"] in mains else " (пропала основная категория)"))
            continue

        def mode(i: Optional[int]) -> Optional[str]:
            vals = [_pct(_num(r[i])) for r in sel if i is not None and len(r) > i and _num(r[i]) is not None]
            return str(Counter(vals).most_common(1)[0][0]) if vals else None

        top = Counter(_pct(_num(r[fbs])) for r in sel).most_common(1)[0][0]
        plan.values[button] = {
            "ozon_category": m["ozon_category"],
            "ozon_rf_pct": top,
            "ozon_rf_source": OZON_SOURCE,
            "ozon_rf_date": d,
            "ozon_rf_detail": {"fbs_over_300": str(top), "fbo_over_300": mode(fbo), "rfbs": mode(rfbs),
                               "types": len(sel), "file": file_name},
        }
    return plan


CN_TIERS = ("0–1500", "1500,01–5000", "5000,01+")


def plan_ozon_cn(data: bytes, file_name: str, today: date) -> Plan:
    d = date_from_filename(file_name)
    if not d:
        raise SyncError(f"в имени файла «{file_name}» нет даты (ДД_ММ_ГГГГ)")
    sheets = read_sheets(data, [OZON_CN_TREE, OZON_CN_FULL])
    tree, full = sheets.get(OZON_CN_TREE) or [], sheets.get(OZON_CN_FULL) or []
    if len(tree) < 3 or len(full) < 3:
        raise SyncError("Ozon Китай: листы пустые")

    def cols(head: list[str]) -> tuple[list[int], list[int]]:
        rf = [i for i, h in enumerate(head) if h.upper().startswith("RFBS")]
        fp = [i for i, h in enumerate(head) if h.upper().startswith("FBP")]
        if len(rf) != 3 or len(fp) != 3:
            raise SyncError("Ozon Китай: не найдены 3 порога rFBS и FBP — структура файла изменилась")
        return rf, fp

    t_rf, t_fp = cols(tree[1])
    f_rf, _ = cols(full[1])
    block_of = {r[3]: r[0] for r in tree[2:] if len(r) > max(t_fp)}
    tiers_of = {r[3]: {"rfbs": [str(_pct(_num(r[i]))) for i in t_rf], "fbp": [str(_pct(_num(r[i]))) for i in t_fp]}
                for r in tree[2:] if len(r) > max(t_fp) and all(_num(r[i]) is not None for i in t_rf + t_fp)}
    types = [r for r in full[2:] if len(r) > max(f_rf) and all(_num(r[i]) is not None for i in f_rf)]
    plan = Plan("ozon_cn", d, OZON_SOURCE, file_name=file_name)
    for button, m in MAPPING.items():
        spec = m["ozon_cn"]
        if not spec:
            continue
        plan.mapped += 1
        cats = spec.get("mp") or [c for c, b in block_of.items() if b == spec.get("block")]
        missing = [c for c in spec.get("mp", []) if c not in block_of]
        for c in missing:
            plan.warnings.append(f"Ozon Китай: категория «{c}» пропала из файла — «{button}»")
        sel = [r for r in types if r[6] in cats]
        if not sel:
            if not missing:
                plan.warnings.append(f"Ozon Китай: нет типов товаров для «{button}» ({spec})")
            continue
        tiers = [statistics.median(_pct(_num(r[i])) for r in sel) for i in f_rf]
        plan.values[button] = {
            "ozon_cn_pct": Decimal(tiers[1]).quantize(Decimal("0.01")),
            "ozon_cn_source": OZON_SOURCE,
            "ozon_cn_date": d,
            "ozon_cn_tiers": {
                "rfbs": [str(Decimal(t).quantize(Decimal("0.01"))) for t in tiers],
                "tiers_rub": list(CN_TIERS),
                "by_category": {c: tiers_of[c] for c in cats if c in tiers_of},
                "types": len(sel), "file": file_name,
            },
        }
    return plan


# --- защита, сравнение, запись ----------------------------------------------------------

def check(plan: Plan, current: dict[str, dict]) -> Plan:
    """Сравнить с текущими данными и проставить problems (защита) и changes."""
    title = TITLES[plan.platform]
    if not plan.values:
        plan.problems.append(f"{title}: не сопоставлено ни одной категории (пустые или битые данные)")
        return plan
    cover = len(plan.values) / plan.mapped if plan.mapped else 0
    if cover < MIN_COVERAGE:
        plan.problems.append(f"{title}: сопоставлено {len(plan.values)} из {plan.mapped} категорий "
                             f"({cover:.0%} < {MIN_COVERAGE:.0%})")
    for button, vals in plan.values.items():
        old = current.get(button) or {}
        for f in (*SHOWN[plan.platform], "wb_fbw_pct"):
            if f not in vals:
                continue
            was, now = old.get(f), vals[f]
            if was is not None and Decimal(was) == Decimal(now):
                continue
            plan.changes.append((button, f, was, now))
            if was is not None and f in SHOWN[plan.platform] and abs(Decimal(now) - Decimal(was)) > MAX_JUMP:
                plan.problems.append(f"{title}: «{button}» {FIELD_TITLES[f]} {fmt_pct(was)} → {fmt_pct(now)} "
                                     f"(больше {MAX_JUMP} п.п.)")
    return plan


async def current_rows() -> dict[str, dict]:
    return {r["button"]: dict(r) for r in await database.list_commissions()}


_COLUMNS = {
    "wb": ("wb_category", "wb_rf_pct", "wb_cn_pct", "wb_fbw_pct", "wb_source", "wb_date"),
    "ozon_rf": ("ozon_category", "ozon_rf_pct", "ozon_rf_source", "ozon_rf_date", "ozon_rf_detail"),
    "ozon_cn": ("ozon_cn_pct", "ozon_cn_source", "ozon_cn_date", "ozon_cn_tiers"),
}
JSON_COLUMNS = {"ozon_rf_detail", "ozon_cn_tiers"}


async def apply(plan: Plan) -> None:
    """Записать площадку (только её колонки + примечание из сопоставления) и дату успешного обновления."""
    assert plan.ok, plan.problems
    cols = _COLUMNS[plan.platform]
    sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols)) + f", note = ${len(cols) + 2}, loaded_at = now()"
    async with database.pool().acquire() as conn:
        async with conn.transaction():
            for button, vals in plan.values.items():
                args = [json.dumps(vals[c], ensure_ascii=False) if c in JSON_COLUMNS else vals[c] for c in cols]
                note = MAPPING[button]["note"] or None
                await conn.execute(f"UPDATE mp_commissions SET {sets} WHERE button = $1", button, *args, note)
    await database.set_setting(f"commissions:{plan.platform}:last_success", shanghai_today().isoformat())
    await database.set_setting(f"commissions:{plan.platform}:data_date", plan.data_date.isoformat())


async def rebuild_kb() -> str:
    """Пересобрать справочник commissions из mp_commissions и загрузить в базу знаний."""
    from services.commissions_kb import build_markdown
    from services.kb_ingest import KB_DIR, KB_SOURCES, ingest

    rows = await database.list_commissions()
    md = build_markdown([dict(r) for r in rows])
    filename, topic = KB_SOURCES["commissions"]
    (KB_DIR / filename).write_text(md, encoding="utf-8")
    r = await ingest("commissions", filename, topic, md.encode("utf-8"))
    return "без изменений" if r.status == "unchanged" else f"{r.chunks} фрагм."


# --- тексты ----------------------------------------------------------------------------

def _v(x) -> str:
    return "—" if x is None else fmt_pct(x)


def changes_text(plan: Plan, limit: Optional[int] = 5) -> list[str]:
    """Строки «категория: РФ 35 → 38, Китай 18 → 20», самые большие изменения первыми."""
    by: dict[str, list] = {}
    for b, f, was, now in plan.changes:
        if f in SHOWN[plan.platform]:
            by.setdefault(b, []).append((f, was, now))

    def size(item):
        return max((abs(Decimal(n) - Decimal(w)) if w is not None else Decimal(100)) for _, w, n in item[1])

    items = sorted(by.items(), key=size, reverse=True)
    lines = [f"• {b}: " + ", ".join(f"{FIELD_TITLES[f]} {_v(w)} → {_v(n)}" for f, w, n in ch)
             for b, ch in (items if limit is None else items[:limit])]
    return lines


def report_text(plan: Plan, applied: bool, kb: Optional[str] = None) -> str:
    title = TITLES[plan.platform]
    head = f"данные на {fmt_date(plan.data_date)} · {plan.source}" + (f" · файл {plan.file_name}" if plan.file_name else "")
    if not plan.ok:
        lines = [f"🚨 Комиссии {title} НЕ обновлены — сработала защита, старые данные остались", head, *plan.problems]
    else:
        n = len(plan.changed_buttons)
        lines = [(f"🔄 Комиссии {title} обновлены" if applied else f"🔎 Комиссии {title} — проверка без записи"), head,
                 f"Категорий: {len(plan.values)} из {plan.mapped} · изменилось: {n}"]
        if n:
            lines += ["Топ-5 изменений (было → стало):", *changes_text(plan, 5)]
        if kb:
            lines.append(f"Справочник в базе знаний: {kb}")
    lines += [f"⚠️ {w}" for w in plan.warnings]
    return "\n".join(lines)


def table_text(plan: Plan, current: dict[str, dict]) -> str:
    """Все категории площадки: было → стало (для dry и превью файла Ozon)."""
    lines = [f"{TITLES[plan.platform]}: все категории (было → стало)"]
    for button in MAPPING:
        vals = plan.values.get(button)
        old = current.get(button) or {}
        if vals is None:
            lines.append(f"{button}: — (не сопоставлено)")
            continue
        parts = [f"{FIELD_TITLES[f]} {_v(old.get(f))} → {_v(vals[f])}" for f in (*SHOWN[plan.platform], "wb_fbw_pct")
                 if f in vals]
        lines.append(f"{button}: " + ", ".join(parts))
    return "\n".join(lines)


async def alarm(bot: Bot, text: str) -> None:
    await notify.send_to_group(bot, "reports", text, parse_mode=None)


# --- сценарии запуска -------------------------------------------------------------------

async def build_wb_plan(today: Optional[date] = None) -> Plan:
    today = today or shanghai_today()
    ru = await fetch_wb("RU")
    cn = await fetch_wb("CN")
    return check(plan_wb(ru, cn, today), await current_rows())


async def run_wb(bot: Bot, dry: bool) -> Plan:
    """WB: получить, проверить, записать (если не dry и защита пропустила), отчёт в «CinC Отчёты»."""
    try:
        plan = await build_wb_plan()
    except SyncError as e:
        plan = Plan("wb", shanghai_today(), WB_SOURCE, problems=[str(e)])
        if not dry:
            await alarm(bot, report_text(plan, applied=False))
        return plan
    if dry:
        return plan
    if plan.ok:
        await apply(plan)
        kb = await rebuild_kb()
        await alarm(bot, report_text(plan, applied=True, kb=kb))
    else:
        await alarm(bot, report_text(plan, applied=False))
    return plan


def build_ozon_plan(data: bytes, file_name: str, current: dict[str, dict]) -> Plan:
    platform = detect_ozon(data, file_name)
    builder = plan_ozon_rf if platform == "ozon_rf" else plan_ozon_cn
    try:
        return check(builder(data, file_name, shanghai_today()), current)
    except XlsxError as e:
        raise SyncError(str(e))


async def apply_and_report(bot: Bot, plan: Plan) -> str:
    await apply(plan)
    kb = await rebuild_kb()
    text = report_text(plan, applied=True, kb=kb)
    await alarm(bot, text)
    return text


def due(last_success: Optional[str], today: date, every_days: int) -> bool:
    if not last_success:
        return True
    try:
        return (today - date.fromisoformat(last_success)).days >= every_days
    except ValueError:
        return True


def ozon_reminder_text(stale: list[str]) -> str:
    lines = [f"📥 Пора обновить комиссии Ozon ({', '.join(stale)}): прошло {config.COMMISSIONS_SYNC_DAYS} дней "
             "с последнего файла. Скачайте и перешлите мне .xlsx:"]
    for title, url in OZON_PAGES:
        lines.append(f"• {title}: {url}")
    lines += ["РФ — файл «Таблица категорий для расчёта вознаграждения» (не «Селект», не Fashion).",
              "Китай — «Таблица категорий» в разделе для продавцов из Китая.",
              "Покажу изменения и спрошу, записывать ли."]
    return "\n".join(lines)


async def scheduled(bot: Bot, today: Optional[date] = None) -> dict:
    """Ежедневно 08:30: WB — если прошло N дней с успешного обновления; Ozon — напоминание админу."""
    today = today or shanghai_today()
    every = config.COMMISSIONS_SYNC_DAYS
    done = {}
    if due(await database.get_setting("commissions:wb:last_success"), today, every):
        done["wb"] = (await run_wb(bot, dry=False)).ok
    stale = [TITLES[p] for p in ("ozon_rf", "ozon_cn")
             if due(await database.get_setting(f"commissions:{p}:last_success"), today, every)]
    if stale and due(await database.get_setting("commissions:ozon:last_reminder"), today, OZON_REMIND_EVERY):
        await notify.notify_admin(bot, ozon_reminder_text(stale))
        await database.set_setting("commissions:ozon:last_reminder", today.isoformat())
        done["ozon_reminder"] = stale
    return done
