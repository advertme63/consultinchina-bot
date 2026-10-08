"""Ядро ответа (ТЗ 4.1): поиск → история → один вызов Claude → запись в messages.
Без Telegram: этим же кодом пользуется scripts/run_tests.py."""
import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import voyageai.error

import database
from config import config
from services.claude_client import ask_claude
from services.limits import shanghai_now
from services.voyage_client import embed_query

logger = logging.getLogger(__name__)

MAX_QUESTION_CHARS = 2000
# Невидимые символы, из которых может состоять «пустое» сообщение
_INVISIBLE_RE = re.compile("[\u00ad\u180e\u200b-\u200f\u2028-\u202f\u205f-\u206f\ufeff]")
# Цена и порядок оплаты регистрации → всегда кнопка «Связаться с менеджером» (решение Ивана 08.10).
# Вопросы об оплате поставщикам сюда не относятся.
_PAYMENT_RE = re.compile(r"оплат|оплачива|предоплат|рассрочк|реквизит|в рублях|как платить|как заплатить", re.I)
_REG_PRICE_RE = re.compile(
    r"(сколько стоит|стоимост|цен[аыу]|прайс|почём).{0,60}(регистрац|открыт|компани|wfoe|пакет|базов|эксперт|профи)"
    r"|(регистрац|открыт|компани|wfoe|пакет|базов|эксперт|профи).{0,60}(сколько стоит|стоимост|цен[аыу])",
    re.I | re.S,
)
_SUPPLIER_RE = re.compile(r"поставщик|фабрик|продавц|1688|таобао|taobao", re.I)
SEARCH_LIMIT = 8
HISTORY_PAIRS = 3  # 6 сообщений
NOT_KB_GAP_INTENTS = ("off_topic", "wants_human")


def clean_question(text: Optional[str]) -> str:
    """Текст вопроса без невидимых символов и пробелов по краям. Пустая строка — вопроса нет."""
    return _INVISIBLE_RE.sub("", text or "").strip()


def needs_manager_button(question: str) -> bool:
    """Вопрос о цене или порядке оплаты регистрации — кнопку менеджера показываем всегда, не полагаясь на cta."""
    if _SUPPLIER_RE.search(question):
        return False
    return bool(_PAYMENT_RE.search(question) or _REG_PRICE_RE.search(question))


@dataclass
class AnswerResult:
    answer: str
    answered_from_kb: bool
    intent: str
    cta: str
    relevant: bool
    best_distance: Optional[float]
    fragments: list = field(default_factory=list)  # строки поиска, лучшие первыми
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    cache_write: int = 0
    latency_ms: int = 0
    message_id: Optional[int] = None
    force_manager: bool = False
    service: str = "none"
    suggestions: list = field(default_factory=list)
    cost_usd: float = 0.0
    disclaimer: bool = False


async def _embed(question: str) -> Optional[list[float]]:
    """Voyage на бесплатном тарифе — 3 запроса в минуту. Упёрлись — ждём и повторяем один раз,
    потом ищем только полнотекстом."""
    for attempt in range(2):
        try:
            return await embed_query(question)
        except voyageai.error.RateLimitError:
            if attempt == 0:
                logger.warning("Voyage: лимит запросов, жду 20 с")
                await asyncio.sleep(20)
        except Exception:
            logger.exception("Voyage: ошибка эмбеддинга, ищу только полнотекстом")
            return None
    logger.warning("Voyage: лимит не отпустил, ищу только полнотекстом")
    return None


def _is_relevant(rows, best_distance: Optional[float]) -> bool:
    """ТЗ 3.4: нерелевантно, только если вектор хуже порога И полнотекст пуст.
    Полнотекст «не пуст», если фрагмент совпал хотя бы по 2 значимым словам запроса
    (или по единственному, если в запросе одно значимое слово)."""
    vector_ok = best_distance is not None and best_distance <= config.RELEVANCE_THRESHOLD
    n_lex = rows[0]["n_lex"] if rows else 0
    need = min(2, n_lex) if n_lex else 1
    fts_ok = any(r["matched"] >= need for r in rows if r["frank"] is not None) if n_lex else False
    return vector_ok or fts_ok


def _user_content(question: str, rows, relevant: bool) -> str:
    today = shanghai_now().strftime("%d.%m.%Y")
    if relevant and rows:
        frags = "\n\n".join(f"<fragment doc_key=\"{r['doc_key']}\">\n{r['content']}\n</fragment>" for r in rows)
        context = f"Фрагменты справочников (по релевантности):\n\n{frags}"
    else:
        context = "Фрагменты справочников: по этому вопросу в базе ничего не найдено."
    return f"Сегодня {today} (Шанхай).\n\n{context}\n\nВопрос клиента:\n{question}"


async def answer_question(telegram_id: int, question: str) -> AnswerResult:
    started = time.monotonic()
    question = clean_question(question)[:MAX_QUESTION_CHARS]
    if not question:
        raise ValueError("пустой вопрос: в поиск и Claude не отправляем")

    embedding = await _embed(question)
    rows = await database.search_hybrid(embedding, question, limit=SEARCH_LIMIT, fts_weight=config.FTS_WEIGHT)
    best_distance = await database.best_vector_distance(embedding) if embedding else None
    relevant = _is_relevant(rows, best_distance)

    history = []
    for h in await database.recent_dialog(telegram_id, HISTORY_PAIRS):
        q, a = clean_question(h["question"]), (h["answer"] or "").strip()
        if q and a:  # пустое сообщение в истории ломает вызов Claude (400)
            history += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]

    reply = await ask_claude(history, _user_content(question, rows, relevant), telegram_id, "answer")
    answered_from_kb = reply.answered_from_kb and relevant
    latency_ms = int((time.monotonic() - started) * 1000)
    doc_keys = list(dict.fromkeys(r["doc_key"] for r in rows if r["doc_key"])) if relevant else []

    # Кнопки (Э4): «Заказать» и подсказки — только для ответа по базе на справочный вопрос
    service = reply.service if answered_from_kb and reply.intent != "off_topic" else "none"
    suggestions = (
        reply.suggestions if service == "none" and reply.intent == "question" and answered_from_kb else []
    )
    u = reply.usage
    message_id = await database.save_message(
        telegram_id, question, reply.answer, doc_keys, best_distance, answered_from_kb,
        reply.intent, reply.cta, u.input, u.output, latency_ms,
        u.cache_read, u.cache_write_5m, u.cache_write_1h,
        None if service == "none" else service, suggestions or None,
    )
    # Пробел в базе — только справочные вопросы: «хочу человека» и вне темы в сводку не идут
    if not answered_from_kb and reply.intent not in NOT_KB_GAP_INTENTS:
        await database.save_unanswered(telegram_id, question, reply.answer)

    return AnswerResult(
        answer=reply.answer,
        answered_from_kb=answered_from_kb,
        intent=reply.intent,
        cta=reply.cta,
        relevant=relevant,
        best_distance=best_distance,
        fragments=list(rows),
        tokens_in=reply.tokens_in,
        tokens_out=reply.tokens_out,
        cache_read=reply.cache_read,
        cache_write=reply.cache_write,
        latency_ms=latency_ms,
        message_id=message_id,
        force_manager=needs_manager_button(question),
        service=service,
        suggestions=suggestions,
        cost_usd=u.cost_usd,
        disclaimer=reply.disclaimer,
    )
