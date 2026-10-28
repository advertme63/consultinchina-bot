"""Э7: комиссии — /sync_commissions [dry] (WB) и файл Ozon .xlsx от админа (превью → «Записать» / «Отмена»)."""
import logging
import secrets
from io import BytesIO

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import config
from services import commission_sync as cs
from services.notify import split_text

logger = logging.getLogger(__name__)

router = Router()
router.message.filter(F.chat.type == "private")
router.message.filter(F.from_user.id == config.ADMIN_TELEGRAM_ID)
router.callback_query.filter(F.from_user.id == config.ADMIN_TELEGRAM_ID)

PENDING: dict[str, cs.Plan] = {}  # превью файла Ozon до «Записать»; после перезапуска бота — прислать файл заново
MAX_FILE = 20 * 1024 * 1024


async def _answer(message: Message, text: str, **kwargs) -> None:
    chunks = split_text(text)
    for n, chunk in enumerate(chunks):
        await message.answer(chunk, parse_mode=None, **(kwargs if n == len(chunks) - 1 else {}))


@router.message(Command("sync_commissions"))
async def sync_commissions_cmd(message: Message, command: CommandObject) -> None:
    dry = (command.args or "").strip().lower() == "dry"
    await message.answer("Запрашиваю комиссии WB (РФ и Китай)…" + (" Без записи." if dry else ""))
    plan = await cs.run_wb(message.bot, dry=dry)
    if not plan.ok:
        await _answer(message, cs.report_text(plan, applied=False) + ("" if dry else "\n\nТревога — в «CinC Отчёты»."))
        return
    text = cs.report_text(plan, applied=not dry)
    if dry:
        text += "\n\n" + cs.table_text(plan, await cs.current_rows()) + "\n\nЗаписать: /sync_commissions"
    else:
        text += "\n\nОтчёт — в «CinC Отчёты»."
    await _answer(message, text)


def _confirm_kb(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Записать", callback_data=f"comsync:apply:{token}"),
        InlineKeyboardButton(text="✖️ Отмена", callback_data=f"comsync:cancel:{token}"),
    ]])


@router.message(StateFilter(None), F.document.func(lambda d: d is not None and (d.file_name or "").lower().endswith(".xlsx")))
async def ozon_file(message: Message) -> None:
    doc = message.document
    if doc.file_size and doc.file_size > MAX_FILE:
        await message.answer(f"Файл больше {MAX_FILE // 1024 // 1024} МБ — не похоже на таблицу комиссий Ozon.")
        return
    await message.answer("Разбираю файл комиссий Ozon…")
    buf = BytesIO()
    await message.bot.download(doc, destination=buf)
    current = await cs.current_rows()
    try:
        plan = cs.build_ozon_plan(buf.getvalue(), doc.file_name, current)
    except cs.SyncError as e:
        await message.answer(f"Файл не принят: {e}", parse_mode=None)
        return
    finally:
        buf.close()
    if not plan.ok:
        text = cs.report_text(plan, applied=False)
        await cs.alarm(message.bot, text)
        await _answer(message, text + "\n\nНичего не записано. Тревога — в «CinC Отчёты».")
        return
    token = secrets.token_hex(4)
    PENDING.clear()  # одно превью за раз: новый файл отменяет прежнее
    PENDING[token] = plan
    text = cs.report_text(plan, applied=False) + "\n\n" + cs.table_text(plan, current)
    await _answer(message, text, reply_markup=_confirm_kb(token))


@router.callback_query(F.data.startswith("comsync:"))
async def ozon_file_decision(callback: CallbackQuery) -> None:
    _, action, token = callback.data.split(":", 2)
    plan = PENDING.pop(token, None)
    await callback.message.edit_reply_markup(reply_markup=None)
    if plan is None:
        await callback.answer("Превью устарело — пришлите файл ещё раз.", show_alert=True)
        return
    if action == "cancel":
        await callback.answer("Отменено")
        await callback.message.answer(f"Комиссии {cs.TITLES[plan.platform]}: ничего не записано.")
        return
    await callback.answer("Записываю…")
    try:
        text = await cs.apply_and_report(callback.bot, plan)
    except Exception as e:
        logger.exception("Запись комиссий %s", plan.platform)
        await callback.message.answer(f"⚠️ Не удалось записать комиссии: {type(e).__name__}: {e}", parse_mode=None)
        return
    await callback.message.answer(text + "\n\nОтчёт — в «CinC Отчёты».", parse_mode=None)
