from aiogram import F, Router
from aiogram.types import Message

from keyboards import BTN_MATERIALS, LEGACY_DOCS
from services.materials import send_materials

router = Router()
router.message.filter(F.chat.type == "private")


@router.message(F.text.in_({BTN_MATERIALS, LEGACY_DOCS}))
async def list_documents(message: Message) -> None:
    await send_materials(message.bot, message.chat.id)
