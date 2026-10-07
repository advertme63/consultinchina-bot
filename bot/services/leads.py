"""Заявки менеджеру и карточки в группу «CinC Лиды» (ТЗ 6.3, 6.4)."""
import json
import logging
from html import escape
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramMigrateToChat
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

import database
from config import config
from services.claude_client import ask_claude_text
from services.qualify import VERDICT_LABELS, answers_line

logger = logging.getLogger(__name__)

LEADS_CHAT_KEY = "leads_chat_id"
CLIENT_THANKS = "Спасибо! Валерий Загурский напишет вам в Telegram в рабочее время (Шанхай, UTC+8)."


async def leads_chat_id() -> Optional[int]:
    saved = await database.get_setting(LEADS_CHAT_KEY)
    if saved:
        return int(saved)
    return config.LEADS_CHAT_ID


async def _notify_admin(bot: Bot, text: str) -> None:
    try:
        await bot.send_message(config.ADMIN_TELEGRAM_ID, text, parse_mode=None)
    except Exception:
        logger.exception("Не удалось написать админу")


async def send_to_group(bot: Bot, text: str, **kwargs) -> Optional[Message]:
    """Отправка в группу лидов. Группа стала супергруппой → сохраняем новый ID в settings и повторяем.
    Любая ошибка — в лог и админу в личку (ТЗ 6.4)."""
    chat_id = await leads_chat_id()
    if not chat_id:
        logger.error("LEADS_CHAT_ID не задан, карточка не отправлена")
        await _notify_admin(bot, "⚠️ LEADS_CHAT_ID не задан — заявка не ушла в группу:\n\n" + text)
        return None
    for attempt in range(2):
        try:
            return await bot.send_message(chat_id, text, **kwargs)
        except TelegramMigrateToChat as e:
            logger.warning("Группа лидов переехала: %s → %s", chat_id, e.migrate_to_chat_id)
            chat_id = e.migrate_to_chat_id
            await database.set_setting(LEADS_CHAT_KEY, str(chat_id))
            await _notify_admin(bot, f"ℹ️ Группа лидов стала супергруппой, новый ID {chat_id} сохранён в settings.")
        except Exception as e:
            logger.exception("Не удалось отправить в группу лидов %s", chat_id)
            await _notify_admin(
                bot, f"⚠️ Не удалось отправить в группу лидов ({type(e).__name__}: {e}).\n\nТекст:\n{text[:3000]}"
            )
            return None
    return None


async def dialog_summary(telegram_id: int) -> str:
    rows = await database.recent_messages(telegram_id, 10)
    if not rows:
        return "Вопросов боту не задавал."
    def cut(t: str, n: int) -> str:
        return t if len(t) <= n else t[:n].rstrip() + " […]"

    dialog = "\n\n".join(f"Клиент: {cut(r['question'], 500)}\nБот: {cut(r['answer'] or '', 800)}" for r in rows)
    prompt = (
        "Ниже — переписка клиента с ботом Consult in China. Сделай для Валерия Загурского резюме в 3–5 коротких "
        "строк: что клиенту нужно, его ситуация и цифры, если он их называл, что уже ответил бот. "
        "Без приветствий, без оценок клиента, без рекомендаций по продаже. Просто текст, без разметки. "
        "Длинные ответы бота сокращены здесь и помечены […] — это не обрыв, не упоминай это.\n\n"
        f"Переписка:\n{dialog}"
    )
    try:
        text, _, _ = await ask_claude_text(prompt, max_tokens=300)
        return text
    except Exception:
        logger.exception("Не удалось получить резюме диалога")
        return "(резюме не получено — см. последние вопросы)"


async def last_questions(telegram_id: int, n: int = 3) -> list[str]:
    rows = await database.recent_messages(telegram_id, n)
    return [r["question"][:150] for r in rows]


def take_keyboard(lead_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✅ Взял в работу", callback_data=f"lead_take:{lead_id}")]]
    )


def card_text(
    lead_id: int,
    user: dict,
    name: str,
    phone: Optional[str],
    answers: Optional[dict],
    verdict: Optional[str],
    summary: str,
    questions: list[str],
    test: bool = False,
) -> str:
    username = f"@{escape(user['username'])}" if user.get("username") else "без username"
    contact = " · ".join(
        p for p in (escape(name), username, f"ID {user['telegram_id']}", escape(phone) if phone else None) if p
    )
    lines = [
        ("🧪 <b>ТЕСТ</b> · " if test else "") + f"🆕 <b>Заявка #{lead_id}</b> · источник: {escape(user.get('source') or '—')}",
        f"Имя: {contact}",
        f"Итог квалификации: «{VERDICT_LABELS[verdict]}»" if verdict else "Итог квалификации: не проходил",
    ]
    if answers:
        lines.append(escape(answers_line(answers)))
    lines.append(f"\n<b>Суть диалога (ИИ):</b>\n{escape(summary)}")
    if questions:
        lines.append("\n<b>Последние вопросы:</b>\n" + "\n".join(f"• {escape(q)}" for q in questions))
    return "\n".join(lines)


async def submit_lead(bot: Bot, user: dict, name: str, phone: Optional[str], test: bool = False) -> int:
    """Новая заявка → карточка в группу; повтор в течение 24 ч → ответ на старую карточку. Возвращает id заявки."""
    uid = user["telegram_id"]
    q = await database.last_qualification(uid)
    payload = json.loads(q["payload"]) if q and isinstance(q["payload"], str) else (q["payload"] if q else None)
    answers = payload.get("answers") if payload else None
    verdict = payload.get("verdict") if payload else None
    answers_json = json.dumps(answers, ensure_ascii=False) if answers else None
    questions = await last_questions(uid)

    old = await database.recent_lead(uid)
    if old:
        await database.update_lead_contact(old["id"], name, phone, answers_json, verdict)
        parts = [f"🔁 <b>Клиент снова написал</b> (заявка #{old['id']})", f"Имя: {escape(name)}" + (f" · {escape(phone)}" if phone else "")]
        if verdict:
            parts.append(f"Итог квалификации: «{VERDICT_LABELS[verdict]}»")
        if questions:
            parts.append("Последние вопросы:\n" + "\n".join(f"• {escape(x)}" for x in questions))
        await send_to_group(bot, "\n".join(parts), reply_to_message_id=old["group_message_id"])
        await database.log_event(uid, "lead", json.dumps({"lead_id": old["id"], "repeat": True}))
        return old["id"]

    summary = await dialog_summary(uid)
    lead_id = await database.create_lead(uid, name, phone, answers_json, verdict, summary)
    text = card_text(lead_id, user, name, phone, answers, verdict, summary, questions, test=test)
    sent = await send_to_group(bot, text, reply_markup=take_keyboard(lead_id))
    if sent:
        await database.set_lead_group_message(lead_id, sent.message_id)
    await database.log_event(uid, "lead", json.dumps({"lead_id": lead_id, "verdict": verdict, "test": test}))
    return lead_id
