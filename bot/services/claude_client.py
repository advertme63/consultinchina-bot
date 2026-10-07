from typing import Optional

from anthropic import AsyncAnthropic

from config import config

_client: Optional[AsyncAnthropic] = None

SYSTEM_PROMPT = (
    "Ты — консультант ConsultInChina, компании по регистрации и сопровождению бизнеса в Китае. "
    "Отвечай на вопросы клиентов только на основе предоставленных фрагментов документов. "
    "Если в фрагментах нет ответа — прямо скажи, что информации недостаточно, и предложи "
    "оставить обращение. Не придумывай факты, цифры и сроки. Отвечай на русском языке, по делу, "
    "структурированно, без лишней воды."
)


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        if not config.ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        _client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


async def generate_answer(question: str, context: str) -> str:
    client = _get_client()
    message = await client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Фрагменты документов:\n\n{context}\n\nВопрос клиента: {question}",
            }
        ],
    )
    for block in message.content:
        if getattr(block, "type", None) == "text":
            return block.text
    return "Не удалось получить текстовый ответ. Попробуйте переформулировать вопрос."
