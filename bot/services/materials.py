"""«📄 Материалы» (Э5): файлы из files_library по sort_order, с чистым именем и подписью «<b>название</b>\\nописание».
Кэш file_id Telegram: после первой успешной отправки шлём по file_id; не вышло — с диска и перезаписываем file_id."""
import logging
import re
from html import escape
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile

import database

logger = logging.getLogger(__name__)
_BAD_NAME_CHARS = re.compile(r'[\\/:*?"<>|]+')


def default_send_name(title: str, original_filename: str) -> str:
    """Consult_in_China_<название>.<расширение исходного файла>, пробелы → _."""
    ext = Path(original_filename).suffix or ".pdf"
    base = _BAD_NAME_CHARS.sub("", title).strip().replace(" ", "_")
    base = re.sub(r"_+", "_", base)
    return f"Consult_in_China_{base}{ext}"


def caption_of(row) -> str:
    cap = f"<b>{escape(row['title'])}</b>"
    if row["description"]:
        cap += f"\n{escape(row['description'])}"
    return cap[:1024]  # лимит подписи Telegram


def clean_name(row) -> str:
    if row["send_name"]:
        return row["send_name"]
    return re.sub(r"^[A-Za-z0-9_-]{10,}_", "", Path(row["storage_path"]).name)  # без префикса file_unique_id


async def send_one(bot: Bot, chat_id: int, row) -> bool:
    caption = caption_of(row)
    if row["tg_file_id"]:
        try:
            await bot.send_document(chat_id, row["tg_file_id"], caption=caption, parse_mode="HTML")
            return True
        except Exception as e:
            logger.warning("Материал #%s: отправка по file_id не удалась (%s) — шлю с диска", row["id"], e)
    path = Path(row["storage_path"])
    if not path.exists():
        logger.error("Материал #%s: файла нет на диске: %s", row["id"], path)
        return False
    sent = await bot.send_document(chat_id, FSInputFile(path, filename=clean_name(row)), caption=caption,
                                   parse_mode="HTML")
    if sent.document:
        await database.set_library_tg_file_id(row["id"], sent.document.file_id)
    return True


async def send_materials(bot: Bot, chat_id: int) -> int:
    rows = await database.list_files_library()
    if not rows:
        await bot.send_message(chat_id, "Пока нет доступных материалов.")
        return 0
    n = 0
    for row in rows:
        try:
            n += await send_one(bot, chat_id, row)
        except Exception:
            logger.exception("Материал #%s: не удалось отправить", row["id"])
    return n
