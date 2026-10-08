"""Сегменты и квалификация (ТЗ 6.1–6.2, Э3.5 «Сегменты», разделы 1–6).

Каждый сегмент: вопросы, правила вердикта (сверху вниз — срабатывает первое), подписи итогов.
Текст итога пишет Claude по правилу; цифры комиссий (селлер) и тариф (уже есть компания) собирает Python.
Пороги и тексты селлера — как утверждены Иваном 07.10.2026 (Э3).
"""
from dataclasses import dataclass, field
from typing import Callable, Optional

import database
from services.claude_client import ask_claude_text

SKIPPED = "—"
MAX_WORDS = 100
# «Вы вправе не согласиться…» — только там, где оценка может огорчить (решение Ивана 08.10)
DISAGREE_VERDICTS = {"unprofitable", "early", "imp_service", "exp_early"}
CATEGORIES = "categories"  # варианты шага берутся из mp_commissions.button


@dataclass(frozen=True)
class Step:
    key: str
    question: str
    options: tuple[str, ...] | str = ()  # () — свободный ответ; CATEGORIES — кнопки из таблицы комиссий
    skippable: bool = False
    columns: int = 1


@dataclass(frozen=True)
class Segment:
    key: str
    label: str  # для карточки заявки: «📦 Импортёр»
    button: str  # кнопка в «Что вас привело?»
    steps: tuple[Step, ...] = ()
    verdict: Optional[Callable[[dict], str]] = None
    labels: dict = field(default_factory=dict)  # вердикт → подпись итога
    rules: dict = field(default_factory=dict)  # вердикт → правило для Claude


# --- 🛒 Селлер (Э3, без изменений, кроме категории кнопками) ----------------------

def _seller_verdict(a: dict) -> str:
    purchases = a.get("china_purchases")
    if purchases == "Нет" and a.get("priority") != "Платежи поставщикам":
        return "unprofitable"
    if a.get("turnover") == "до 1 млн ₽":
        return "early"
    if purchases == "Да, регулярно" and a.get("turnover") in ("3–5 млн ₽", "5–10 млн ₽", "10+ млн ₽"):
        return "fit"
    return "review"


SELLER = Segment(
    key="seller",
    label="🛒 Селлер",
    button="🛒 Продаю на маркетплейсах",
    steps=(
        Step("marketplace", "Где продаёте?", ("WB", "Ozon", "WB и Ozon", "Пока не продаю")),
        Step("china_purchases", "Закупаетесь в Китае?", ("Да, регулярно", "Иногда", "Нет")),
        Step("category", "Какая категория товара?", CATEGORIES, columns=2),
        Step("turnover", "Оборот в месяц?", ("до 1 млн ₽", "1–3 млн ₽", "3–5 млн ₽", "5–10 млн ₽", "10+ млн ₽")),
        Step("tax_regime", "Налоговый режим?", ("УСН 6%", "УСН 15%", "ООО на ОСН (с НДС)", "Другое / не знаю")),
        Step("priority", "Что важнее всего?", ("Платежи поставщикам", "Маржа и налоги", "Выход на другие рынки", "Другое")),
    ),
    verdict=_seller_verdict,
    labels={
        "fit": "Похоже, подходит",
        "review": "Нужен разбор",
        "early": "Пока рано",
        "unprofitable": "Пока, скорее всего, невыгодно",
    },
    rules={
        "unprofitable": "не закупается в Китае и главное для него — не платежи поставщикам. Объясни: основная польза "
        "китайской компании — платежи и закупки в Китае.",
        "early": "оборот до 1 млн ₽ в месяц. Объясни: затраты на владение компанией (первый год — от 19 000 CNY, "
        "дальше — бухгалтерия от 8 500 CNY в год плюс юридический адрес) могут съесть выгоду.",
        "fit": "закупается в Китае регулярно и оборот от 3 млн ₽ в месяц. Предложи разбор экономики с нашим специалистом "
        "на цифрах клиента.",
        "review": "профиль не попадает в однозначные правила. Предложи разбор экономики с нашим специалистом.",
    },
)


# --- 📦 Импортёр -------------------------------------------------------------------

IMPORTER_COMPANY_PAINS = ("Оплата", "Документы", "Конфиденциальность поставщиков")


def _importer_verdict(a: dict) -> str:
    if a.get("frequency") in ("Разово", "Только начинаю"):
        return "imp_service"
    if a.get("frequency") == "Регулярно" and a.get("pain") in IMPORTER_COMPANY_PAINS:
        return "imp_company"
    return "imp_review"


IMPORTER = Segment(
    key="importer",
    label="📦 Импортёр",
    button="📦 Закупаю в Китае",
    steps=(
        Step("goods", "Что закупаете? Напишите одним сообщением или нажмите «Пропустить».", skippable=True),
        Step("frequency", "Как часто закупаете?", ("Регулярно", "Разово", "Только начинаю")),
        Step("volume", "Объём закупок в месяц?", ("до 1 млн ₽", "1–5 млн ₽", "5–20 млн ₽", "20+ млн ₽")),
        Step("payment", "Как платите поставщикам сейчас?", ("Через агента", "Платёжный агент", "Картой", "Другое")),
        Step("pain", "Что сейчас больше всего мешает?",
             ("Оплата", "Проверка поставщика", "Логистика и таможня", "Документы", "Конфиденциальность поставщиков")),
        Step("ru_entity", "Есть юрлицо в РФ или РБ?", ("Да", "Нет")),
    ),
    verdict=_importer_verdict,
    labels={
        "imp_service": "Начните с услуги",
        "imp_company": "Своя компания может дать вам больше — нужен разбор",
        "imp_review": "Нужен разбор",
    },
    rules={
        "imp_service": "закупки разовые или клиент только начинает. Компания в Китае пока не нужна — предложи начать "
        "с услуги; назови 1–3 подходящие к ответам клиента: экспресс-подбор поставщиков — 1 250 CNY за товар, "
        "расширенный подбор с переговорами — 2 500–4 200 CNY за товар, дистанционная проверка поставщика — "
        "1 700–2 900 CNY за компанию, выездная проверка производства — от 3 300 CNY за визит + расходы на выезд, "
        "выкуп товара — 5% от суммы закупки. Диапазон называй диапазоном, в рубли не пересчитывай.",
        "imp_company": "закупает регулярно, и главное, что мешает, — оплата, документы или конфиденциальность "
        "поставщиков. Объясни, что может дать своя компания: одна юрисдикция с поставщиком, платежи в юанях с фапьяо, "
        "ваши поставщики не на виду у посредников и агентов (база поставщиков остаётся у вас). Не пиши, что поставщик "
        "не узнает получателя или конечного покупателя — это стоп-лист. Предложи разбор с нашим специалистом.",
        "imp_review": "профиль не попадает в однозначные правила. Предложи разбор с нашим специалистом.",
    },
)


# --- 🌏 Выход на рынок Китая ------------------------------------------------------

EXPANSION = Segment(
    key="expansion",
    label="🌏 Выход на рынок",
    button="🌏 Хочу выйти на рынок Китая",
    steps=(
        Step("activity", "Вид деятельности?", ("Производство", "Торговля", "Услуги", "IT")),
        Step("goal", "Цель в Китае?",
             ("Представительство", "Продажи в Китае", "Закупочный хаб", "Производство", "Международные расчёты")),
        Step("stage", "На какой вы стадии?", ("Идея", "Пилот", "Работающий бизнес со своим продуктом")),
        Step("turnover", "Годовой оборот компании?", ("до 50 млн ₽", "50–300 млн ₽", "300 млн ₽+", "Не скажу")),
        Step("timing", "Сроки?", ("до 3 мес", "до года", "Изучаю")),
        Step("meetings", "Нужны встречи на месте?", ("Да", "Нет")),
    ),
    verdict=lambda a: "exp_ready" if a.get("stage") == "Работающий бизнес со своим продуктом" else "exp_early",
    labels={"exp_ready": "Готовы к разбору", "exp_early": "Пока рано — вот что стоит знать"},
    rules={
        "exp_ready": "работающий бизнес со своим продуктом. Предложи разбор с нашим специалистом: цель в Китае "
        "и подходящая форма присутствия.",
        "exp_early": "стадия идеи или пилота. Дай 2–3 главных пункта из фрагментов справочников ниже, которые полезны "
        "на этой стадии, и предложи задавать вопросы боту; к разбору с нашим специалистом можно вернуться, когда "
        "бизнес будет готов. Про международные расчёты — без «обхода санкций».",
    },
)


# --- 🏢 Уже есть компания в Китае ------------------------------------------------

def _service_verdict(a: dict) -> str:
    if a.get("reporting") == "Есть долги":
        return "svc_debts"
    if a.get("need") == "Сменить бухгалтера":
        return "svc_switch"
    if a.get("need") == "Ликвидация":
        return "svc_liquidation"
    return "svc_other"


SERVICE = Segment(
    key="service",
    label="🏢 Своя компания",
    button="🏢 У меня уже есть компания в Китае",
    steps=(
        Step("form", "Какая у вас форма компании?", ("WFOE", "Представительство", "Гонконг")),
        Step("need", "Что нужно?", ("Сменить бухгалтера", "Налоги", "Найм", "Банк", "Ликвидация")),
        Step("operations", "Сколько операций в месяц?", ("до 12", "до 30", "до 60", "больше")),
        Step("reporting", "Отчётность в порядке?", ("Да", "Не уверен", "Есть долги")),
        Step("city", "В каком городе компания? Напишите или нажмите «Пропустить».", skippable=True),
    ),
    verdict=_service_verdict,
    labels={
        "svc_debts": "Сразу разбор — есть долги по отчётности",
        "svc_switch": "Переход на нашу бухгалтерию",
        "svc_liquidation": "Ликвидация — нужен разбор",
        "svc_other": "Ответ по справочнику и разбор",
    },
    rules={
        "svc_debts": "у компании долги по отчётности. Тариф не называй — сразу предложи разбор с нашим специалистом: "
        "сначала нужно понять состояние учёта.",
        "svc_switch": "хочет сменить бухгалтера. Коротко опиши, как проходит переход на нашу бухгалтерию, по фрагментам "
        "ниже. Тарифы, стоимость сопровождения и аудита не называй — бот добавит их отдельным блоком.",
        "svc_liquidation": "нужна ликвидация. Коротко опиши сценарий по фрагментам справочника «Ликвидация WFOE» "
        "и предложи разбор с нашим специалистом.",
        "svc_other": "нужна помощь: {need}. Коротко ответь по фрагментам справочников ниже и предложи разбор "
        "с нашим специалистом.",
    },
)

OTHER = Segment(key="other", label="💬 Вопрос", button="💬 Просто задать вопрос")

SEGMENTS: dict[str, Segment] = {s.key: s for s in (SELLER, IMPORTER, EXPANSION, SERVICE, OTHER)}
QUALIFIED = (SELLER, IMPORTER, EXPANSION, SERVICE)
VERDICT_LABELS: dict[str, str] = {k: v for s in QUALIFIED for k, v in s.labels.items()}
VERDICT_SEGMENT: dict[str, str] = {k: s.key for s in QUALIFIED for k in s.labels}


def segment_of_verdict(verdict: Optional[str]) -> Optional[str]:
    return VERDICT_SEGMENT.get(verdict or "")


async def step_options(step: Step) -> tuple[str, ...]:
    if step.options == CATEGORIES:
        return tuple(r["button"] for r in await database.list_commissions())
    return step.options  # type: ignore[return-value]


def answers_line(answers: dict, segment: str = "seller") -> str:
    """«WB и Ozon · Да, регулярно · Посуда · 3–5 млн ₽ · УСН 6% · Платежи поставщикам»"""
    seg = SEGMENTS.get(segment) or SELLER
    return " · ".join(str(answers.get(s.key) or SKIPPED) for s in seg.steps)


# --- фрагменты справочников для итогов, которым нужен текст из базы -------------------

_SERVICE_NEED_DOCS = {
    "Сменить бухгалтера": ["accounting_switch"],
    "Налоги": ["tax"],
    "Найм": ["employment"],
    "Банк": ["registration", "wfoe"],
    "Ликвидация": ["liquidation"],
}


async def _fragments(query: str, doc_keys: list[str], limit: int = 4) -> list[str]:
    from services.answer import _embed  # тот же эмбеддинг с повтором при лимите Voyage

    emb = await _embed(query)
    rows = await database.search_hybrid(emb, query, limit=limit, doc_keys=doc_keys)
    return [r["content"] for r in rows]


async def _context(segment: str, verdict: str, a: dict) -> list[str]:
    if verdict == "exp_early":
        return await _fragments(f"{a.get('goal', '')} {a.get('activity', '')} форма компании в Китае",
                                ["wfoe", "entity_choice"])
    if verdict in ("svc_switch", "svc_liquidation", "svc_other"):
        need = a.get("need", "")
        return await _fragments(f"{need} {a.get('form', '')}", _SERVICE_NEED_DOCS.get(need, ["registration"]))
    return []


async def verdict_text(
    segment: str, answers: dict, verdict: str, telegram_id: int | None = None
) -> tuple[str, int, int]:
    """Текст итога от Claude по правилу (ТЗ 6.2, Э3.5 раздел 6): до 100 слов, 1–2 фактора, стоп-лист."""
    seg = SEGMENTS[segment]
    label = seg.labels[verdict]
    rule = seg.rules[verdict].format(need=answers.get("need", ""))
    lines = "\n".join(f"- {s.question.split('?')[0]}: {answers.get(s.key) or 'не указано'}" for s in seg.steps)
    fragments = await _context(segment, verdict, answers)
    context = (
        "\n\nФрагменты справочников для итога:\n\n"
        + "\n\n".join(f"<fragment>\n{f}\n</fragment>" for f in fragments)
        if fragments else ""
    )
    closing = (
        "«Вы вправе не согласиться с этой оценкой — обсудите свою ситуацию с нашим специалистом, кнопка ниже.»"
        if verdict in DISAGREE_VERDICTS
        else "«Обсудить с нашим специалистом — кнопка ниже.» (без фразы «вправе не согласиться»)"
    )
    extra = []
    if segment == "seller":
        extra.append("- ставки комиссий маркетплейсов не называй: бот добавит их отдельным блоком под твоим текстом;")
    if verdict == "svc_switch":
        extra.append("- тариф бухгалтерии и стоимость аудита не называй: бот добавит их отдельным блоком;")
    prompt = (
        "Клиент прошёл проверку «Подходит ли мне компания в Китае». Напиши ему итог.\n\n"
        f"Ответы клиента:\n{lines}\n\n"
        f"Итог по правилам компании: «{label}» — {rule}"
        f"{context}\n\n"
        "Требования к тексту:\n"
        f"- первая строка — итог жирным: **{label}**;\n"
        f"- строго до {MAX_WORDS} слов всего, на «вы», голосом компании;\n"
        "- не пересказывай все ответы клиента: назови только 1–2 фактора, на которых держится итог;\n"
        "- не предлагай пакеты и тарифы; следующий шаг — только разбор с нашим специалистом;\n"
        "- никаких утверждений о налогах, учёте и налоговых режимах в России;\n"
        "- не пиши категоричнее итога: «скорее всего», «может», а не «точно», «не окупится»;\n"
        "- никаких сумм выгоды и экономии в рублях, процентов роста маржи и обещаний прибыли;\n"
        "- цены — только из публичного прайса и только если без них не объяснить итог;\n"
        + "".join(f"{x}\n" for x in extra)
        + f"- в конце ровно одно предложение, не повторяй его: {closing}\n"
        "- не вызывай инструменты, просто текст."
    )
    text, tin, tout = await ask_claude_text(prompt, max_tokens=600, telegram_id=telegram_id, purpose="verdict")
    words = len(text.split())
    if words > MAX_WORDS:  # один повтор: длиннее лимита — просим сократить
        text2, tin2, tout2 = await ask_claude_text(
            prompt + f"\n\nПрошлый вариант получился {words} слов — это больше {MAX_WORDS}. Сократи до 80–90 слов, "
            "сохрани первую строку и финальное предложение.\n\nПрошлый вариант:\n" + text,
            max_tokens=600,
            telegram_id=telegram_id,
            purpose="verdict_retry",
        )
        text, tin, tout = text2, tin + tin2, tout + tout2
    return text, tin, tout


# --- блоки, которые собирает Python (цифры не от Claude) -------------------------------

TARIFFS = {
    "до 12": ("START-MINI", "8 500", "до 12 операций в месяц"),
    "до 30": ("START", "12 000", "до 30 операций в месяц"),
    "до 60": ("GROW", "18 000", "до 60 операций в месяц"),
    "больше": ("COMPLIANCE+", "35 000", "несколько валют и банков, ВЭД"),
}
AUDIT_PRICE = "2 000"


def tariff_block(answers: dict) -> str:
    """Тариф бухгалтерии — только для WFOE: тарифы в прайсе — для китайских компаний (решение Ивана 08.10)."""
    if answers.get("form") != "WFOE":
        return "Стоимость сопровождения назовёт наш специалист."
    name, price, cond = TARIFFS.get(answers.get("operations") or "", TARIFFS["больше"])
    text = (
        f"<b>Тариф по вашему объёму:</b> {name} — {price} CNY в год ({cond}).\n"
        f"Перед приёмом дел — первичный аудит, {AUDIT_PRICE} CNY."
    )
    if answers.get("reporting") == "Не уверен":
        text += "\nАудит покажет точное состояние учёта."
    return text


async def python_block(segment: str, verdict: str, answers: dict) -> str:
    """HTML-блок под текстом Claude: комиссии (селлер) или тариф (переход на бухгалтерию)."""
    if segment == "seller":
        from services.commissions import seller_block

        return await seller_block(answers)
    if verdict == "svc_switch":
        return tariff_block(answers)
    return ""
