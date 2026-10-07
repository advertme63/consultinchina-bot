import os
from datetime import date, datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import database
from config import config
from services.pdf_ingest import chunk_text, extract_text
from services.voyage_client import embed_documents
from states import AddFileStates, UploadStates

router = Router()
router.message.filter(F.from_user.id == config.ADMIN_TELEGRAM_ID)
router.callback_query.filter(F.from_user.id == config.ADMIN_TELEGRAM_ID)

TOPICS = ["регистрация", "бухгалтерия", "ликвидация", "маркетплейсы", "визы", "другое"]
DOC_TYPES = ["справочник", "кейс", "разовый вопрос"]


def topics_keyboard() -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=t, callback_data=f"topic:{t}")] for t in TOPICS]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def types_keyboard() -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=t, callback_data=f"doctype:{t}")] for t in DOC_TYPES]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# --- access management -----------------------------------------------------

@router.message(Command("grant_trial"))
async def grant_trial(message: Message) -> None:
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Использование: /grant_trial <telegram_id> <дней>")
        return
    try:
        target_id, days = int(parts[1]), int(parts[2])
    except ValueError:
        await message.answer("ID и количество дней должны быть числами.")
        return
    expires = datetime.now(timezone.utc) + timedelta(days=days)
    await database.ensure_user(target_id, None)
    await database.set_role(target_id, "trial", expires)
    await message.answer(f"Пользователю {target_id} выдан trial на {days} дн. (до {expires:%d.%m.%Y}).")


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


# --- knowledge base upload ---------------------------------------------------

@router.message(Command("upload_doc"))
async def upload_doc_start(message: Message, state: FSMContext) -> None:
    await state.set_state(UploadStates.waiting_file)
    await message.answer("Пришлите PDF-файл для загрузки в справочник.")


@router.message(UploadStates.waiting_file, F.document)
async def upload_doc_file(message: Message, state: FSMContext) -> None:
    if not message.document.file_name.lower().endswith(".pdf"):
        await message.answer("Нужен файл в формате PDF.")
        return
    file = await message.bot.get_file(message.document.file_id)
    local_path = f"/app/data/uploads/{message.document.file_unique_id}.pdf"
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    await message.bot.download_file(file.file_path, local_path)
    await state.update_data(file_path=local_path, filename=message.document.file_name)
    await state.set_state(UploadStates.waiting_topic)
    await message.answer("Выберите тему документа:", reply_markup=topics_keyboard())


@router.callback_query(UploadStates.waiting_topic, F.data.startswith("topic:"))
async def upload_doc_topic(callback: CallbackQuery, state: FSMContext) -> None:
    topic = callback.data.split(":", 1)[1]
    await state.update_data(topic=topic)
    await state.set_state(UploadStates.waiting_type)
    await callback.message.edit_text(f"Тема: {topic}\nВыберите тип документа:", reply_markup=types_keyboard())
    await callback.answer()


@router.callback_query(UploadStates.waiting_type, F.data.startswith("doctype:"))
async def upload_doc_type(callback: CallbackQuery, state: FSMContext) -> None:
    doc_type = callback.data.split(":", 1)[1]
    data = await state.get_data()
    await callback.message.edit_text("Обрабатываю документ, это может занять минуту…")
    await callback.answer()

    text = extract_text(data["file_path"])
    chunks = chunk_text(text)
    if not chunks:
        await callback.message.answer("Не удалось извлечь текст из PDF.")
        await state.clear()
        return

    try:
        embeddings = await embed_documents(chunks)
    except Exception:
        await callback.message.answer(
            "Не удалось получить эмбеддинги (проверьте VOYAGE_API_KEY в .env). Документ не сохранён."
        )
        await state.clear()
        return

    version_date = date.today()
    document_id = await database.create_document(
        data["filename"], data["topic"], doc_type, version_date, callback.from_user.id
    )
    await database.insert_chunks(
        document_id, data["topic"], doc_type, version_date, list(zip(chunks, embeddings))
    )
    await callback.message.answer(
        f"Готово: «{data['filename']}» загружен, тема «{data['topic']}», тип «{doc_type}», "
        f"{len(chunks)} фрагментов."
    )
    await state.clear()


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
