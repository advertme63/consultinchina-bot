import logging
import os
import re
import shutil
from datetime import datetime
from html import escape
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import database
from config import config
from keyboards import BTN_MATERIALS, LEGACY_DOCS
from services.kb_ingest import KB_DIR, KB_SOURCES, ingest
from states import AddFileStates, UploadStates

logger = logging.getLogger(__name__)

UPLOADS_DIR = Path("/app/data/uploads")
KB_PREV_DIR = Path("/app/data/kb_prev")
KB_EXTRA_DIR = Path("/app/data/kb_extra")

router = Router()
router.message.filter(F.chat.type == "private")
router.message.filter(F.from_user.id == config.ADMIN_TELEGRAM_ID)
router.callback_query.filter(F.from_user.id == config.ADMIN_TELEGRAM_ID)

# --- access management -----------------------------------------------------

@router.message(Command("grant_full"))
async def grant_full(message: Message) -> None:
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: /grant_full <telegram_id>")
        return
    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer("ID должен быть числом.")
        return
    await database.ensure_user(target_id, None)
    await database.set_role(target_id, "client", None)
    await message.answer(f"Пользователю {target_id} открыт полный доступ (клиент).")


@router.message(Command("revoke"))
async def revoke(message: Message) -> None:
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: /revoke <telegram_id>")
        return
    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer("ID должен быть числом.")
        return
    await database.set_role(target_id, "none", None)
    await message.answer(f"Доступ пользователя {target_id} закрыт.")


@router.message(Command("status"))
async def status(message: Message) -> None:
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: /status <telegram_id>")
        return
    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer("ID должен быть числом.")
        return
    user = await database.get_user(target_id)
    if not user:
        await message.answer("Пользователь не найден.")
        return
    trial_info = f", до {user['trial_expires_at']:%d.%m.%Y}" if user["trial_expires_at"] else ""
    await message.answer(f"ID {target_id}: роль {user['role']}{trial_info}")


@router.message(Command("list_access"))
async def list_access(message: Message) -> None:
    rows = await database.list_active_access()
    if not rows:
        await message.answer("Активных доступов нет.")
        return
    lines = []
    for r in rows:
        trial_info = f" (до {r['trial_expires_at']:%d.%m.%Y})" if r["trial_expires_at"] else ""
        lines.append(f"{r['telegram_id']} @{r['username'] or '—'} — {r['role']}{trial_info}")
    await message.answer("\n".join(lines))


# --- knowledge base: /upload_doc, /docs, /delete_doc ----------------------------

DOC_KEY_RE = re.compile(r"^[a-z0-9_]{2,32}$")


def doc_keys_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=f"{k} — {topic}", callback_data=f"dockey:{k}")]
        for k, (_, topic) in KB_SOURCES.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("upload_doc"))
async def upload_doc_start(message: Message, state: FSMContext) -> None:
    await state.set_state(UploadStates.waiting_file)
    await message.answer("Пришлите файл .md или .pdf для базы знаний. Отмена — /cancel.")


@router.message(Command("cancel"), StateFilter(UploadStates))
async def upload_doc_cancel(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("tmp_path"):
        Path(data["tmp_path"]).unlink(missing_ok=True)
    await state.clear()
    await message.answer("Загрузка отменена.")


@router.message(UploadStates.waiting_file, F.document)
async def upload_doc_file(message: Message, state: FSMContext) -> None:
    name = message.document.file_name or "file"
    ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    if ext not in ("md", "pdf"):
        await message.answer("Нужен файл .md или .pdf.")
        return
    tmp_path = UPLOADS_DIR / f"tmp_{message.document.file_unique_id}.{ext}"
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    await message.bot.download(message.document, destination=tmp_path)
    await state.update_data(tmp_path=str(tmp_path), filename=name, ext=ext)
    await state.set_state(UploadStates.waiting_key)
    await message.answer(
        "Выберите doc_key из таблицы или напишите новый (латиница, цифры, _). "
        "Документ с тем же ключом будет заменён.",
        reply_markup=doc_keys_keyboard(),
    )


@router.callback_query(UploadStates.waiting_key, F.data.startswith("dockey:"))
async def upload_doc_key_button(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await _handle_doc_key(callback.message, state, callback.data.split(":", 1)[1], callback.from_user.id)


@router.message(UploadStates.waiting_key, F.text, ~F.text.startswith("/"))
async def upload_doc_key_text(message: Message, state: FSMContext) -> None:
    await _handle_doc_key(message, state, message.text.strip().lower(), message.from_user.id)


async def _handle_doc_key(message: Message, state: FSMContext, doc_key: str, user_id: int) -> None:
    if not DOC_KEY_RE.match(doc_key):
        await message.answer("Ключ: 2–32 символа, латиница в нижнем регистре, цифры и _. Попробуйте ещё раз.")
        return
    data = await state.get_data()
    if doc_key in KB_SOURCES:
        if data["ext"] != "md":
            await message.answer("Для ключей из таблицы справочников нужен .md. Пришлите другой ключ или /cancel.")
            return
        await state.update_data(doc_key=doc_key, user_id=user_id)
        await _ingest_upload(message, state, KB_SOURCES[doc_key][1])
        return
    existing = await database.get_document_by_key(doc_key)
    await state.update_data(doc_key=doc_key, user_id=user_id)
    if existing:
        await _ingest_upload(message, state, existing["topic"])
        return
    await state.set_state(UploadStates.waiting_topic)
    await message.answer(f"Новый ключ <b>{escape(doc_key)}</b>. Напишите тему документа (например: визы, найм).")


@router.message(UploadStates.waiting_topic, F.text, ~F.text.startswith("/"))
async def upload_doc_topic(message: Message, state: FSMContext) -> None:
    await _ingest_upload(message, state, message.text.strip()[:100])


async def _ingest_upload(message: Message, state: FSMContext, topic: str) -> None:
    data = await state.get_data()
    await state.clear()
    tmp_path = Path(data["tmp_path"])
    doc_key, ext = data["doc_key"], data["ext"]
    filename = KB_SOURCES[doc_key][0] if doc_key in KB_SOURCES else data["filename"]
    await message.answer("Обрабатываю документ. Из-за лимита Voyage это может занять до пары минут…")
    try:
        if ext == "pdf":
            final_path = UPLOADS_DIR / f"{doc_key}.pdf"
            os.replace(tmp_path, final_path)
            result = await ingest(doc_key, filename, topic, final_path.read_bytes(), data["user_id"], str(final_path))
        else:
            raw = tmp_path.read_bytes()
            result = await ingest(doc_key, filename, topic, raw, data["user_id"])
            if result.status == "loaded":
                _store_md(doc_key, filename, raw)
    except Exception as e:
        logger.exception("upload_doc failed")
        await message.answer(f"Ошибка, документ не сохранён: {escape(type(e).__name__)}: {escape(str(e)[:300])}")
        return
    finally:
        tmp_path.unlink(missing_ok=True)

    if result.status == "unchanged":
        await message.answer(f"<b>{escape(doc_key)}</b>: файл не изменился, ничего не делаю.")
        return
    action = "заменён" if result.replaced else "загружен"
    await message.answer(
        f"Готово: <b>{escape(doc_key)}</b> {action} — «{escape(filename)}», тема «{escape(topic)}», "
        f"{result.chunks} фрагм."
    )


def _store_md(doc_key: str, filename: str, raw: bytes) -> None:
    """Справочник из таблицы — на место файла в data/kb/ (старый в data/kb_prev/), чтобы ingest_kb.py
    не откатил загрузку. Новые ключи — в data/kb_extra/."""
    if doc_key in KB_SOURCES:
        target = KB_DIR / filename
        if target.exists():
            KB_PREV_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, KB_PREV_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{filename}")
    else:
        target = KB_EXTRA_DIR / f"{doc_key}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)


async def kb_documents_text() -> str:
    rows = await database.list_documents()
    if not rows:
        return "В базе знаний нет документов."
    lines = [f"<b>База знаний: {len(rows)} док., {sum(r['chunks'] for r in rows)} фрагм.</b>"]
    for r in rows:
        key = escape(r["doc_key"]) if r["doc_key"] else f"id {r['id']} (без ключа)"
        lines.append(f"• <b>{key}</b> — {escape(r['filename'])} · {r['version_date']:%d.%m.%Y} · {r['chunks']} фрагм.")
    return "\n".join(lines)


@router.message(Command("docs"))
async def docs_list(message: Message) -> None:
    await message.answer(await kb_documents_text())


@router.message(F.text.in_({BTN_MATERIALS, LEGACY_DOCS}))
async def admin_materials(message: Message) -> None:
    """Админу «📄 Материалы» — справочники, по которым отвечает бот, и презентации для пользователей."""
    files = await database.list_files_library()
    if files:
        library = "\n".join(f"• {escape(f['title'])}" for f in files)
    else:
        library = "пусто — добавить: /add_file"
    await message.answer(
        f"{await kb_documents_text()}\n\n<b>Материалы для пользователей ({len(files)}):</b>\n{library}"
    )


@router.message(Command("delete_doc"))
async def delete_doc(message: Message) -> None:
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: /delete_doc <doc_key>")
        return
    doc_key = parts[1].lower()
    filename = await database.delete_document_by_key(doc_key)
    if not filename:
        await message.answer(f"Документа с ключом <b>{escape(doc_key)}</b> нет.")
        return
    note = (
        "\nФайл в data/kb/ остался: ingest_kb.py загрузит его снова." if doc_key in KB_SOURCES else ""
    )
    await message.answer(f"Удалён <b>{escape(doc_key)}</b> ({escape(filename)}) вместе с фрагментами.{note}")


# --- documents library -------------------------------------------------------

@router.message(Command("add_file"))
async def add_file_start(message: Message, state: FSMContext) -> None:
    await state.set_state(AddFileStates.waiting_file)
    await message.answer(
        "Пришлите файл для библиотеки «Документы». Подпись к файлу станет его названием "
        "(если не указать — возьму имя файла)."
    )


@router.message(AddFileStates.waiting_file, F.document)
async def add_file_save(message: Message, state: FSMContext) -> None:
    file = await message.bot.get_file(message.document.file_id)
    local_path = f"/app/data/library/{message.document.file_unique_id}_{message.document.file_name}"
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    await message.bot.download_file(file.file_path, local_path)
    title = message.caption or message.document.file_name
    await database.add_file_to_library(message.document.file_name, local_path, title)
    await message.answer(f"Файл «{title}» добавлен в библиотеку документов.")
    await state.clear()


# --- Э4: статистика и анализ -----------------------------------------------------------

def _days_arg(message: Message, default: int, allowed: tuple[int, ...] | None = None) -> int | None:
    parts = (message.text or "").split()
    if len(parts) < 2:
        return default
    try:
        n = int(parts[1])
    except ValueError:
        return None
    if allowed and n not in allowed:
        return None
    return n if 1 <= n <= 365 else None


@router.message(Command("stats"))
async def stats_cmd(message: Message) -> None:
    from services.notify import split_text
    from services.stats import period_report

    days = _days_arg(message, 7, (7, 30))
    if days is None:
        await message.answer("Использование: /stats [7|30]")
        return
    for chunk in split_text(await period_report(days)):
        await message.answer(chunk, parse_mode=None)


@router.message(Command("digest"))
async def digest_cmd(message: Message) -> None:
    """Сводка новых вопросов сейчас (не дожидаясь 19:00) — в личку админу; вопросы помечаются отправленными."""
    from services.digest import build_digest
    from services.notify import split_text

    await message.answer("Собираю сводку новых вопросов…")
    text = await build_digest()
    if not text:
        await message.answer("Новых вопросов без ответа из базы нет.")
        return
    for chunk in split_text(text):
        await message.answer(chunk, parse_mode=None)


@router.message(Command("report"))
async def report_cmd(message: Message) -> None:
    """Анализ недели сейчас, не дожидаясь понедельника — в личку админу."""
    from services.notify import split_text
    from services.weekly import build_weekly

    await message.answer("Готовлю анализ недели, это займёт до минуты…")
    for chunk in split_text(await build_weekly()):
        await message.answer(chunk, parse_mode=None)


@router.message(Command("export"))
async def export_cmd(message: Message) -> None:
    from services.export import build_csv

    days = _days_arg(message, 7)
    if days is None:
        await message.answer("Использование: /export <дней>, например /export 7")
        return
    data, n = await build_csv(days)
    await message.answer_document(
        BufferedInputFile(data, filename=f"cinc_dialogs_{days}d.csv"),
        caption=f"Диалоги за {days} дн.: {n} строк (без тестовых и админа; № пользователя вместо Telegram ID).",
    )
