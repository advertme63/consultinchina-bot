"""Заявки менеджеру и карточки в группу «CinC Лиды» (ТЗ 6.3, 6.4)."""
import json
import logging
from html import escape
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

import database
from config import config
from services import notify
from services.catalog import service_title
from services.claude_client import ask_claude_text
from services.segments import SEGMENTS, VERDICT_LABELS, answers_line, segment_of_verdict

logger = logging.getLogger(__name__)

CLIENT_THANKS = "Спасибо! Наш специалист напишет вам в Telegram в рабочее время (Шанхай, UTC+8)."


async def leads_chat_id() -> Optional[int]:
    return await notify.chat_id_for("leads")


async def send_to_group(bot: Bot, text: str, **kwargs) -> Optional[Message]:
    """Отправка в «CinC Лиды» — через общий services/notify (migrate_to_chat_id, ошибки → админу)."""
    return await notify.send_to_group(bot, "leads", text, **kwargs)


async def reply_or_new_card(bot: Bot, old, reply_text: str, full_card: str) -> Optional[Message]:
    """Повтор заявки: ответ на старую карточку. Если её удалили в группе («message to be replied not found») —
    полная новая карточка без reply, group_message_id обновляется; админу ошибку не шлём (это не сбой)."""
    try:
        return await notify.send_to_group(bot, "leads", reply_text, raise_bad_markup=True,
                                          reply_to_message_id=old["group_message_id"])
    except TelegramBadRequest as e:
        if "replied not found" not in str(e).lower() and "reply message not found" not in str(e).lower():
            logger.exception("Повтор заявки #%s: ошибка отправки", old["id"])
            await notify.notify_admin(bot, f"⚠️ Не удалось отправить повтор заявки #{old['id']} в группу лидов ({e}).")
            return None
        logger.info("Карточка заявки #%s удалена в группе — отправляю новую полную", old["id"])
        sent = await send_to_group(bot, full_card, reply_markup=take_keyboard(old["id"]))
        if sent:
            await database.set_lead_group_message(old["id"], sent.message_id)
        return sent


async def dialog_summary(telegram_id: int) -> str:
    rows = await database.recent_messages(telegram_id, 10)
    if not rows:
        return "Вопросов боту не задавал."
    def cut(t: str, n: int) -> str:
        return t if len(t) <= n else t[:n].rstrip() + " […]"

    dialog = "\n\n".join(f"Клиент: {cut(r['question'], 500)}\nБот: {cut(r['answer'] or '', 800)}" for r in rows)
    prompt = (
        "Ниже — переписка клиента с ботом Consult in China. Сделай для специалиста Consult in China резюме в 3–5 коротких "
        "строк: что клиенту нужно, его ситуация и цифры, если он их называл, что уже ответил бот. "
        "Без приветствий, без оценок клиента, без рекомендаций по продаже. Просто текст, без разметки. "
        "Длинные ответы бота сокращены здесь и помечены […] — это не обрыв, не упоминай это.\n\n"
        f"Переписка:\n{dialog}"
    )
    try:
        text, _, _ = await ask_claude_text(prompt, max_tokens=300, telegram_id=telegram_id, purpose="summary")
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
    segment: Optional[str] = None,
) -> str:
    username = f"@{escape(user['username'])}" if user.get("username") else "без username"
    contact = " · ".join(
        p for p in (escape(name), username, f"ID {user['telegram_id']}", escape(phone) if phone else None) if p
    )
    lines = [
        ("🧪 <b>ТЕСТ</b> · " if test else "") + f"🆕 <b>Заявка #{lead_id}</b> · "
        + (f"{SEGMENTS[segment].label} · " if segment in SEGMENTS else "")
        + f"источник: {escape(user.get('source') or '—')}",
        f"Имя: {contact}",
        f"Итог квалификации: «{VERDICT_LABELS[verdict]}»" if verdict else "Итог квалификации: не проходил",
    ]
    if answers:
        lines.append(escape(answers_line(answers, segment or "seller")))
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
    # сегмент — из квалификации, на которой основана заявка; иначе — выбранный пользователем
    segment = (payload.get("segment") if payload else None) or segment_of_verdict(verdict) or user["segment"]
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
        full = "🔁 <b>Клиент снова написал</b> (прежняя карточка удалена)\n" + card_text(
            old["id"], user, name, phone, answers, verdict, old["summary"] or "—", questions, segment=segment)
        await reply_or_new_card(bot, old, "\n".join(parts), full)
        await database.log_event(uid, "lead", json.dumps({"lead_id": old["id"], "repeat": True}))
        return old["id"]

    summary = await dialog_summary(uid)
    lead_id = await database.create_lead(uid, name, phone, answers_json, verdict, summary, segment)
    text = card_text(lead_id, user, name, phone, answers, verdict, summary, questions, test=test, segment=segment)
    sent = await send_to_group(bot, text, reply_markup=take_keyboard(lead_id))
    if sent:
        await database.set_lead_group_message(lead_id, sent.message_id)
    await database.log_event(uid, "lead", json.dumps({"lead_id": lead_id, "verdict": verdict, "test": test}))
    return lead_id


ORDER_NO_QUESTION = "исходный вопрос недоступен"


def order_card_text(
    lead_id: int, user: dict, name: str, phone: Optional[str], service: str, question: Optional[str],
    summary: str, segment: Optional[str], test: bool = False,
) -> str:
    username = f"@{escape(user['username'])}" if user.get("username") else "без username"
    contact = " · ".join(
        p for p in (escape(name), username, f"ID {user['telegram_id']}", escape(phone) if phone else None) if p
    )
    title = service_title(service) or service
    lines = [
        ("🧪 <b>ТЕСТ</b> · " if test else "") + f"🛒 <b>Заказ: {escape(title)}</b> · заявка #{lead_id} · "
        + (f"{SEGMENTS[segment].label} · " if segment in SEGMENTS else "")
        + f"источник: {escape(user.get('source') or '—')}",
        f"Имя: {contact}",
        f"\n<b>Вопрос клиента:</b>\n{escape(question) if question else ORDER_NO_QUESTION}",
        f"\n<b>Суть диалога (ИИ):</b>\n{escape(summary)}",
    ]
    return "\n".join(lines)


async def submit_order(
    bot: Bot, user: dict, name: str, phone: Optional[str], service: str, message_id: Optional[int], test: bool = False
) -> int:
    """Заказ через кнопку «📝 Заказать» (Э4): без квалификации. Повтор за 24 ч — ответ на любую прошлую карточку."""
    uid = user["telegram_id"]
    msg = await database.get_message(message_id) if message_id else None
    question = msg["question"] if msg and msg["telegram_id"] == uid else None
    title = service_title(service) or service
    segment = user["segment"]

    old = await database.recent_lead(uid)
    if old:
        await database.update_lead_contact(old["id"], name, phone, None, None)
        parts = [
            f"🔁 <b>Клиент снова написал: 🛒 Заказ: {escape(title)}</b> (заявка #{old['id']})",
            f"Имя: {escape(name)}" + (f" · {escape(phone)}" if phone else ""),
            f"Вопрос клиента: {escape(question) if question else ORDER_NO_QUESTION}",
        ]
        full = "🔁 <b>Клиент снова написал</b> (прежняя карточка удалена)\n" + order_card_text(
            old["id"], user, name, phone, service, question, old["summary"] or "—", segment)
        await reply_or_new_card(bot, old, "\n".join(parts), full)
        await database.log_event(uid, "order", json.dumps({"service": service, "message_id": message_id,
                                                            "lead_id": old["id"], "repeat": True}))
        return old["id"]

    summary = await dialog_summary(uid)
    lead_id = await database.create_lead(uid, name, phone, None, None, summary, segment, service)
    text = order_card_text(lead_id, user, name, phone, service, question, summary, segment, test=test)
    sent = await send_to_group(bot, text, reply_markup=take_keyboard(lead_id))
    if sent:
        await database.set_lead_group_message(lead_id, sent.message_id)
    await database.log_event(uid, "order", json.dumps({"service": service, "message_id": message_id,
                                                        "lead_id": lead_id, "test": test}))
    return lead_id
