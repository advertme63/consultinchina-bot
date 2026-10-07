from typing import Optional

import database
from services.claude_client import generate_answer
from services.voyage_client import embed_query


async def answer_question(question: str, topic_hint: Optional[str] = None) -> tuple[str, bool]:
    query_embedding = await embed_query(question)
    rows = await database.search_chunks(query_embedding, topic=topic_hint, limit=8)

    if not rows:
        return (
            "К сожалению, в справочнике пока нет информации по вашему вопросу.",
            False,
        )

    context = "\n\n---\n\n".join(
        f"[Тема: {r['topic']}, версия от {r['version_date']}]\n{r['content']}" for r in rows
    )
    answer = await generate_answer(question, context)
    return answer, True
