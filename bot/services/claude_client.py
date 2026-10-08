"""Один вызов Claude со структурированным ответом через tool use (ТЗ 4.2, 4.5)."""
from dataclasses import dataclass
from typing import Optional

from anthropic import AsyncAnthropic

from config import config
from services import usage as usage_mod
from services.catalog import SERVICES
from services.prompt import system_prompt

_client: Optional[AsyncAnthropic] = None

INTENTS = ("question", "wants_calc", "wants_human", "off_topic")
CTAS = ("none", "qualify", "manager")
MAX_SUGGESTIONS = 2
MAX_SUGGESTION_CHARS = 50

REPLY_TOOL = {
    "name": "reply",
    "description": "Ответ клиенту в Telegram и метаданные ответа. Вызывается всегда, ровно один раз.",
    "input_schema": {
        "type": "object",
        "properties": {
            "answer": {"type": "string", "description": "Текст ответа клиенту. Разметка — только **жирный** и списки."},
            "answered_from_kb": {
                "type": "boolean",
                "description": "true — ответ опирается на фрагменты справочников или прайс; false — ответа в материалах нет или он неполный.",
            },
            "intent": {"type": "string", "enum": list(INTENTS)},
            "cta": {"type": "string", "enum": list(CTAS)},
            "service": {
                "type": "string",
                "enum": list(SERVICES) + ["none"],
                "description": "Ключ услуги, ТОЛЬКО если клиент спрашивает о цене, составе или заказе конкретной услуги; иначе none.",
            },
            "suggestions": {
                "type": "array",
                "maxItems": MAX_SUGGESTIONS,
                "items": {"type": "string", "maxLength": MAX_SUGGESTION_CHARS},
                "description": "0–2 коротких вопроса от лица клиента — что он логично спросит дальше; только по темам из фрагментов, без продажи.",
            },
        },
        "required": ["answer", "answered_from_kb", "intent", "cta", "service", "suggestions"],
    },
}


@dataclass
class ClaudeReply:
    answer: str
    answered_from_kb: bool
    intent: str
    cta: str
    tokens_in: int  # весь вход (свежий + кеш) — для run_tests/e2e
    tokens_out: int
    cache_read: int
    cache_write: int
    service: str = "none"
    suggestions: list = None
    usage: "usage_mod.Usage" = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        if not config.ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        # Таймаут 60 с, 1 повтор (SDK повторяет при 429, 5xx и обрыве соединения)
        _client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY, timeout=60.0, max_retries=1)
    return _client


def _clean_suggestions(raw) -> list[str]:
    out = []
    for x in raw if isinstance(raw, list) else []:
        t = " ".join(str(x).split())[:MAX_SUGGESTION_CHARS].strip()
        if t and t not in out:
            out.append(t)
    return out[:MAX_SUGGESTIONS]


async def ask_claude(
    history: list[dict], user_content: str, telegram_id: Optional[int] = None, purpose: str = "answer"
) -> ClaudeReply:
    client = _get_client()
    message = await client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=1024,
        system=[{"type": "text", "text": system_prompt(), "cache_control": {"type": "ephemeral"}}],
        tools=[REPLY_TOOL],
        tool_choice={"type": "tool", "name": "reply"},
        messages=history + [{"role": "user", "content": user_content}],
    )
    u = usage_mod.from_api(message.usage)
    await usage_mod.record(u, purpose, telegram_id)
    data: dict = {}
    for block in message.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "reply":
            data = block.input or {}
            break
    answer = str(data.get("answer") or "").strip()
    if not answer:
        raise RuntimeError(f"Claude не вернул ответ (stop_reason={message.stop_reason})")
    service = data.get("service") if data.get("service") in SERVICES else "none"
    return ClaudeReply(
        answer=answer,
        answered_from_kb=bool(data.get("answered_from_kb", False)),
        intent=data.get("intent") if data.get("intent") in INTENTS else "question",
        cta=data.get("cta") if data.get("cta") in CTAS else "none",
        tokens_in=u.total_in,
        tokens_out=u.output,
        cache_read=u.cache_read,
        cache_write=u.cache_write_5m + u.cache_write_1h,
        service=service,
        suggestions=_clean_suggestions(data.get("suggestions")),
        usage=u,
    )


async def ask_claude_text(
    user_content: str, max_tokens: int = 600, telegram_id: Optional[int] = None, purpose: str = "text"
) -> tuple[str, int, int]:
    """Свободный текст с тем же системным промптом (голос, стоп-лист, прайс). → (текст, весь вход, выход)."""
    client = _get_client()
    message = await client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=max_tokens,
        # claude-sonnet-5 по умолчанию сначала «думает»: на длинном промпте размышление съедало весь max_tokens,
        # и текста в ответе не было (Э4, анализ недели). Для свободных текстов размышление выключаем.
        thinking={"type": "disabled"},
        system=[{"type": "text", "text": system_prompt(), "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_content}],
    )
    u = usage_mod.from_api(message.usage)
    await usage_mod.record(u, purpose, telegram_id)
    text = "".join(b.text for b in message.content if getattr(b, "type", None) == "text").strip()
    if not text:
        raise RuntimeError(f"Claude не вернул текст (stop_reason={message.stop_reason})")
    return text, u.total_in, u.output
