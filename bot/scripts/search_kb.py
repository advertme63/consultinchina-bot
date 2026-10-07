"""Проверка поиска: 3 ближайших фрагмента на запрос.

Запуск: docker compose exec bot python scripts/search_kb.py "вопрос" ["вопрос" ...]
"""
import asyncio
import sys

import voyageai.error

sys.path.insert(0, "/app")

import database
from services.voyage_client import embed_query


async def main() -> None:
    await database.init_pool()
    try:
        for i, q in enumerate(sys.argv[1:]):
            if i:
                await asyncio.sleep(21)  # Voyage: 3 запроса в минуту
            for attempt in range(3):
                try:
                    emb = await embed_query(q)
                    break
                except voyageai.error.RateLimitError:
                    print("(лимит Voyage, жду 65 с)", flush=True)
                    await asyncio.sleep(65)
            else:
                raise RuntimeError("Voyage: лимит не отпускает")
            async with database.pool().acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT d.doc_key, c.content, c.embedding <=> $1::vector AS distance
                    FROM chunks c JOIN documents d ON d.id = c.document_id
                    ORDER BY c.embedding <=> $1::vector LIMIT 3
                    """,
                    database._embedding_to_pg(emb),
                )
            print(f"\n=== «{q}»")
            for r in rows:
                path, _, body = r["content"].partition("\n\n")
                print(f"[{r['distance']:.3f}] {r['doc_key'] or 'старый PDF'} | {path}")
                print("    " + " ".join(body.split())[:200])
    finally:
        await database.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
