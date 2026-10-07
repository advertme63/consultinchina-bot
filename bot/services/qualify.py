"""Квалификация «Подходит ли мне компания в Китае» (ТЗ 6.1, 6.2)."""
from dataclasses import dataclass
from typing import Optional

from services.claude_client import ask_claude_text


@dataclass(frozen=True)
class Step:
    key: str
    question: str
    options: tuple[str, ...]  # пусто — свободный ответ


STEPS: tuple[Step, ...] = (
    Step("marketplace", "Где продаёте?", ("WB", "Ozon", "WB и Ozon", "Пока не продаю")),
    Step("china_purchases", "Закупаетесь в Китае?", ("Да, регулярно", "Иногда", "Нет")),
    Step("category", "Какая категория товара? Напишите одним сообщением или нажмите «Пропустить».", ()),
    Step("turnover", "Оборот в месяц?", ("до 1 млн ₽", "1–3 млн ₽", "3–5 млн ₽", "5–10 млн ₽", "10+ млн ₽")),
    Step("tax_regime", "Налоговый режим?", ("УСН 6%", "УСН 15%", "ООО на ОСН (с НДС)", "Другое / не знаю")),
    Step("priority", "Что важнее всего?", ("Платежи поставщикам", "Маржа и налоги", "Выход на другие рынки", "Другое")),
)

SKIPPED = "—"

FIT, REVIEW, EARLY, UNPROFITABLE = "fit", "review", "early", "unprofitable"
VERDICT_LABELS = {
    FIT: "Похоже, подходит",
    REVIEW: "Нужен разбор",
    EARLY: "Пока рано",
    UNPROFITABLE: "Пока, скорее всего, невыгодно",
}
_VERDICT_RULES = {
    UNPROFITABLE: "не закупается в Китае и главное для него — не платежи поставщикам. Объясни: основная польза "
    "китайской компании — платежи и закупки в Китае.",
    EARLY: "оборот до 1 млн ₽ в месяц. Объясни: затраты на владение компанией (первый год — от 19 000 CNY, "
    "дальше — бухгалтерия от 8 500 CNY в год плюс юридический адрес) могут съесть выгоду.",
    FIT: "закупается в Китае регулярно и оборот от 3 млн ₽ в месяц. Предложи разбор экономики с Валерием Загурским.",
    REVIEW: "профиль не попадает в однозначные правила. Предложи разбор экономики с Валерием Загурским.",
}


def verdict(answers: dict) -> str:
    """Правила таблицы 6.2, сверху вниз — срабатывает первое (пороги утверждены Иваном 07.10.2026)."""
    purchases = answers.get("china_purchases")
    if purchases == "Нет" and answers.get("priority") != "Платежи поставщикам":
        return UNPROFITABLE
    if answers.get("turnover") == "до 1 млн ₽":
        return EARLY
    if purchases == "Да, регулярно" and answers.get("turnover") in ("3–5 млн ₽", "5–10 млн ₽", "10+ млн ₽"):
        return FIT
    return REVIEW


def answers_line(answers: dict) -> str:
    """«WB и Ozon · закупает регулярно · посуда · 3–5 млн ₽ · УСН 6% · платежи поставщикам»"""
    return " · ".join(str(answers.get(s.key) or SKIPPED) for s in STEPS)


async def verdict_text(answers: dict, v: str) -> tuple[str, int, int]:
    """Текст итога от Claude: по правилу и ответам, со стоп-листом, без сумм выгоды в рублях."""
    lines = "\n".join(f"- {s.question.split('?')[0]}: {answers.get(s.key) or 'не указано'}" for s in STEPS)
    prompt = (
        "Клиент прошёл проверку «Подходит ли мне компания в Китае». Напиши ему итог.\n\n"
        f"Ответы клиента:\n{lines}\n\n"
        f"Итог по правилам компании: «{VERDICT_LABELS[v]}» — {_VERDICT_RULES[v]}\n\n"
        "Требования к тексту:\n"
        f"- первая строка — итог жирным: **{VERDICT_LABELS[v]}**;\n"
        "- 60–140 слов, на «вы», голосом компании; опирайся на ответы клиента, объясни почему;\n"
        "- никаких сумм выгоды и экономии в рублях, процентов роста маржи и обещаний прибыли;\n"
        "- цены — только из публичного прайса, и только если они нужны для объяснения;\n"
        "- в конце одно предложение: клиент вправе не согласиться с оценкой и обсудить свою ситуацию "
        "с Валерием Загурским — кнопка ниже;\n"
        "- не вызывай инструменты, просто текст."
    )
    return await ask_claude_text(prompt, max_tokens=600)


def parse_answers(payload: Optional[dict]) -> tuple[Optional[dict], Optional[str]]:
    if not payload:
        return None, None
    return payload.get("answers"), payload.get("verdict")
