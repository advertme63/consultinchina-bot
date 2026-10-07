"""Один вызов Claude со структурированным ответом через tool use (ТЗ 4.2, 4.5)."""
from dataclasses import dataclass
from typing import Optional

from anthropic import AsyncAnthropic

from config import config
from services.prompt import system_prompt

_client: Optional[AsyncAnthropic] = None

INTENTS = ("question", "wants_calc", "wants_human", "off_topic")
CTAS = ("none", "qualify", "manager")

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
        },
        "required": ["answer", "answered_from_kb", "intent", "cta"],
    },
}


@dataclass
class ClaudeReply:
    answer: str
    answered_from_kb: bool
    intent: str
    cta: str
    tokens_in: int
    tokens_out: int
    cache_read: int
    cache_write: int


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        if not config.ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        # Таймаут 60 с, 1 повтор (SDK повторяет при 429, 5xx и обрыве соединения)
        _client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY, timeout=60.0, max_retries=1)
    return _client


async def ask_claude(history: list[dict], user_content: str) -> ClaudeReply:
    client = _get_client()
    message = await client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=1024,
        system=[{"type": "text", "text": system_prompt(), "cache_control": {"type": "ephemeral"}}],
        tools=[REPLY_TOOL],
        tool_choice={"type": "tool", "name": "reply"},
        messages=history + [{"role": "user", "content": user_content}],
    )
    data: dict = {}
    for block in message.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "reply":
            data = block.input or {}
            break
    u = message.usage
    cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
    answer = str(data.get("answer") or "").strip()
    if not answer:
        raise RuntimeError(f"Claude не вернул ответ (stop_reason={message.stop_reason})")
    return ClaudeReply(
        answer=answer,
        answered_from_kb=bool(data.get("answered_from_kb", False)),
        intent=data.get("intent") if data.get("intent") in INTENTS else "question",
        cta=data.get("cta") if data.get("cta") in CTAS else "none",
        tokens_in=u.input_tokens + cache_read + cache_write,
        tokens_out=u.output_tokens,
        cache_read=cache_read,
        cache_write=cache_write,
    )
