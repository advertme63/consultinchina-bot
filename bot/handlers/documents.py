from aiogram import F, Router
from aiogram.types import FSInputFile, Message

import database
from keyboards import BTN_MATERIALS, LEGACY_DOCS

router = Router()
router.message.filter(F.chat.type == "private")


@router.message(F.text.in_({BTN_MATERIALS, LEGACY_DOCS}))
async def list_documents(message: Message) -> None:
    files = await database.list_files_library()
    if not files:
        await message.answer("Пока нет доступных документов.")
        return
    for f in files:
        await message.answer_document(FSInputFile(f["storage_path"]), caption=f["title"])
