import asyncio
import sys

sys.path.insert(0, "/app")

import database
from services.rag import answer_question


async def main():
    await database.init_pool()
    answer, found = await answer_question("Какие есть варианты ликвидации WFOE в Китае?")
    print("FOUND:", found)
    print(answer)
    await database.close_pool()


asyncio.run(main())
