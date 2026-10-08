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
from aiogram.types import (
    BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message,
    ReplyKeyboardMarkup,
)

import database
from config import config
from keyboards import BTN_MATERIALS, LEGACY_DOCS, MAIN_MENU
from services.kb_ingest import KB_DIR, KB_SOURCES, ingest
from states import AddFileStates, EditFileStates, UploadStates

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
        library = "\n".join(
            f"• #{f['id']} · №{f['sort_order'] if f['sort_order'] is not None else '—'} · {escape(f['title'])}" for f in files
        ) + "\n\nКак видит клиент: /materials · добавить: /add_file · править: /edit_file <id>"
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

LIBRARY_DIR = Path("/app/data/library")
SKIP = "-"


def _reply_kb(*rows: list[str]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t) for t in r] for r in rows],
                               resize_keyboard=True, one_time_keyboard=True)


async def _save_library_document(message: Message) -> tuple[str, str]:
    """Файл из Telegram → data/library/<file_unique_id>_<имя> (префикс — защита от совпадений имён)."""
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    name = message.document.file_name or "file.pdf"
    path = LIBRARY_DIR / f"{message.document.file_unique_id}_{name}"
    await message.bot.download(message.document, destination=path)
    return name, str(path)


@router.message(Command("add_file"))
async def add_file_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AddFileStates.waiting_file)
    await message.answer("Пришлите файл для раздела «📄 Материалы». Отмена — /cancel_file.")


@router.message(Command("cancel_file"), StateFilter(AddFileStates, EditFileStates))
async def add_file_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.", reply_markup=MAIN_MENU)


@router.message(AddFileStates.waiting_file, F.document)
async def add_file_doc(message: Message, state: FSMContext) -> None:
    name, path = await _save_library_document(message)
    await state.update_data(filename=name, storage_path=path)
    await state.set_state(AddFileStates.title)
    hint = message.caption or Path(name).stem.replace("_", " ")
    await message.answer("Название материала (жирным в подписи):", reply_markup=_reply_kb([hint]))


@router.message(AddFileStates.title, F.text, ~F.text.startswith("/"))
async def add_file_title(message: Message, state: FSMContext) -> None:
    await state.update_data(title=message.text.strip()[:200])
    await state.set_state(AddFileStates.description)
    await message.answer("Описание — одна строка под названием. «-» — без описания.", reply_markup=_reply_kb([SKIP]))


@router.message(AddFileStates.description, F.text, ~F.text.startswith("/"))
async def add_file_description(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    await state.update_data(description=None if text == SKIP else text[:500])
    await state.set_state(AddFileStates.sort_order)
    nxt = await database.next_library_sort_order()
    await message.answer("Номер в списке (меньше — выше):", reply_markup=_reply_kb([str(nxt)]))


@router.message(AddFileStates.sort_order, F.text, ~F.text.startswith("/"))
async def add_file_sort(message: Message, state: FSMContext) -> None:
    if not message.text.strip().isdigit():
        await message.answer("Нужно целое число, например 4.")
        return
    await state.update_data(sort_order=int(message.text.strip()))
    data = await state.get_data()
    await state.set_state(AddFileStates.send_name)
    from services.materials import default_send_name

    await message.answer("Имя файла, которое увидит клиент:",
                         reply_markup=_reply_kb([default_send_name(data["title"], data["filename"])]))


@router.message(AddFileStates.send_name, F.text, ~F.text.startswith("/"))
async def add_file_finish(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    send_name = message.text.strip()[:200]
    if "." not in send_name:
        send_name += Path(data["filename"]).suffix or ".pdf"
    new_id = await database.add_file_to_library(
        data["filename"], data["storage_path"], data["title"], data["description"], data["sort_order"], send_name
    )
    await message.answer(
        f"Добавлено: #{new_id} · №{data['sort_order']} · <b>{escape(data['title'])}</b> · {escape(send_name)}\n"
        f"Проверить, как видит клиент: /materials · править: /edit_file {new_id}",
        reply_markup=MAIN_MENU,
    )


@router.message(Command("materials"))
async def admin_materials_preview(message: Message) -> None:
    """Админу — ровно то, что видит клиент в «📄 Материалы»."""
    from services.materials import send_materials

    await send_materials(message.bot, message.chat.id)


EDIT_FIELDS = {"title": "название", "description": "описание", "sort_order": "номер в списке", "send_name": "имя файла"}


def _file_card(row) -> str:
    return (f"Материал #{row['id']}\n№ {row['sort_order'] if row['sort_order'] is not None else '—'}\n"
            f"Название: <b>{escape(row['title'])}</b>\nОписание: {escape(row['description'] or '—')}\n"
            f"Имя файла: {escape(row['send_name'] or row['filename'])}\nФайл на сервере: {escape(row['filename'])}")


@router.message(Command("edit_file"))
async def edit_file_start(message: Message, state: FSMContext) -> None:
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: /edit_file <id> — id видно в «📄 Материалы».")
        return
    row = await database.get_library_file(int(parts[1]))
    if not row:
        await message.answer("Материала с таким id нет.")
        return
    await state.clear()
    buttons = [[InlineKeyboardButton(text=f"✏️ {t}", callback_data=f"fedit:{row['id']}:{f}")] for f, t in EDIT_FIELDS.items()]
    buttons.append([InlineKeyboardButton(text="📎 Заменить файл", callback_data=f"fedit:{row['id']}:file")])
    await message.answer(_file_card(row), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.regexp(r"^fedit:\d+:(title|description|sort_order|send_name|file)$"))
async def edit_file_field(callback: CallbackQuery, state: FSMContext) -> None:
    _, fid, field = callback.data.split(":")
    await callback.answer()
    await state.clear()
    await state.update_data(edit_id=int(fid), edit_field=field)
    if field == "file":
        await state.set_state(EditFileStates.file)
        await callback.message.answer("Пришлите новый файл. Отмена — /cancel_file.")
        return
    await state.set_state(EditFileStates.value)
    hint = " «-» — очистить." if field == "description" else ""
    await callback.message.answer(f"Новое значение — {EDIT_FIELDS[field]}.{hint} Отмена — /cancel_file.")


@router.message(EditFileStates.value, F.text, ~F.text.startswith("/"))
async def edit_file_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    field, text = data["edit_field"], message.text.strip()
    if field == "sort_order":
        if not text.isdigit():
            await message.answer("Нужно целое число.")
            return
        value = int(text)
    elif field == "description":
        value = None if text == SKIP else text[:500]
    else:
        value = text[:200]
    await state.clear()
    await database.update_library_file(data["edit_id"], field, value)
    await message.answer("Сохранено.\n\n" + _file_card(await database.get_library_file(data["edit_id"])))


@router.message(EditFileStates.file, F.document)
async def edit_file_document(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    name, path = await _save_library_document(message)
    await database.replace_library_file(data["edit_id"], name, path)  # tg_file_id обнуляется
    await message.answer("Файл заменён (кэш Telegram сброшен).\n\n" + _file_card(await database.get_library_file(data["edit_id"])))


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


async def _answer_markdown(message: Message, text: str) -> None:
    """Текст от Claude в личку админу: Telegram-HTML кусками; отказ разметки — без неё."""
    from aiogram.exceptions import TelegramBadRequest
    from services.notify import split_text
    from services.tg_format import to_plain, to_telegram_html

    try:
        for chunk in split_text(to_telegram_html(text, limit=None)):
            await message.answer(chunk)
    except TelegramBadRequest:
        for chunk in split_text(to_plain(text, limit=None)):
            await message.answer(chunk, parse_mode=None)


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
    await _answer_markdown(message, text)


@router.message(Command("report"))
async def report_cmd(message: Message) -> None:
    """Анализ недели сейчас, не дожидаясь понедельника — в личку админу."""
    from services.notify import split_text
    from services.weekly import build_weekly

    await message.answer("Готовлю анализ недели, это займёт до минуты…")
    await _answer_markdown(message, await build_weekly())


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
