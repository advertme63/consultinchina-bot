import asyncio
import sys
import traceback

sys.path.insert(0, "/app")

import database
from services.rag import answer_question


async def main():
    await database.init_pool()
    try:
        answer, found = await answer_question("Какая корпоративная налоговая ставка?")
        print("FOUND:", found)
        print(answer)
    except Exception:
        print("EXCEPTION:")
        traceback.print_exc()
    await database.close_pool()


asyncio.run(main())
