"""E2E-симулятор бота: поддельные Update → настоящий Dispatcher → перехват всех вызовов Bot API.

Реальные БД, поиск (Voyage + pgvector) и Claude — используются. В Telegram не уходит ничего:
  - у Bot фиктивный токен и своя сессия FakeSession — запросы к Bot API не доходят до сети;
  - предохранитель на aiohttp/httpx: любой запрос к api.telegram.org падает и считается нарушением.

Запуск — scripts/e2e/run.sh (см. README.md рядом). Внутри контейнера:
  python scripts/e2e/simulate.py <сценарии.json> <папка_вывода>
  python scripts/e2e/simulate.py --cleanup        — только удалить тестовых пользователей

Тестовые пользователи: telegram_id −2001…−2099, source = 'test'. Удаляются до и после прогона
из messages, leads, events, unanswered_questions, tickets, users.
"""
import asyncio
import html as html_lib
import json
import logging
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, "/app")

from aiogram import Bot, Dispatcher
from aiogram.client.default import Default, DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteWebhook,
    EditMessageReplyMarkup,
    EditMessageText,
    SendChatAction,
    SendDocument,
    SendMessage,
)
from aiogram.types import (
    CallbackQuery,
    Chat,
    Contact,
    Document,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
    User,
)

import database
from config import config

ID_MIN, ID_MAX = -2099, -2001
FAKE_TOKEN = "1000000001:E2E_FAKE_TOKEN_no_network_ever"
BOT_USER = User(id=1000000001, is_bot=True, first_name="CinC E2E", username="cinc_e2e_bot")
# Стоимость: цены из .env (USD за 1 млн токенов)
PRICE_IN, PRICE_OUT, PRICE_CACHE_READ, PRICE_CACHE_WRITE = (  # из .env через config
    config.PRICE_INPUT, config.PRICE_OUTPUT, config.PRICE_CACHE_READ, config.PRICE_CACHE_WRITE_5M)
VOYAGE_INTERVAL = 21.0  # бесплатный Voyage — 3 запроса в минуту; держим паузу, чтобы поиск не падал в полнотекст

log = logging.getLogger("e2e")


# --- учёт расходов и предохранитель сети ----------------------------------------------

class Stats:
    claude_calls = 0
    tokens_in = 0
    tokens_out = 0
    cache_read = 0
    cache_write = 0
    voyage_calls = 0
    telegram_network_attempts: list[str] = []

    @classmethod
    def cost(cls) -> float:
        return (cls.tokens_in * PRICE_IN + cls.tokens_out * PRICE_OUT + cls.cache_read * PRICE_CACHE_READ
                + cls.cache_write * PRICE_CACHE_WRITE) / 1e6


def install_patches() -> None:
    import aiohttp
    import httpx
    from anthropic.resources.messages import AsyncMessages

    orig_create = AsyncMessages.create

    async def counted_create(self, *args, **kwargs):
        r = await orig_create(self, *args, **kwargs)
        u = r.usage
        Stats.claude_calls += 1
        Stats.tokens_in += u.input_tokens
        Stats.tokens_out += u.output_tokens
        Stats.cache_read += getattr(u, "cache_read_input_tokens", 0) or 0
        Stats.cache_write += getattr(u, "cache_creation_input_tokens", 0) or 0
        return r

    AsyncMessages.create = counted_create

    orig_aiohttp = aiohttp.ClientSession._request

    async def guarded_aiohttp(self, method, str_or_url, *args, **kwargs):
        if "telegram.org" in str(str_or_url):
            Stats.telegram_network_attempts.append(f"aiohttp {method} {str_or_url}")
            raise RuntimeError("E2E: сетевой запрос к Telegram заблокирован")
        return await orig_aiohttp(self, method, str_or_url, *args, **kwargs)

    aiohttp.ClientSession._request = guarded_aiohttp

    orig_httpx = httpx.AsyncClient.send

    async def guarded_httpx(self, request, *args, **kwargs):
        if "telegram.org" in str(request.url):
            Stats.telegram_network_attempts.append(f"httpx {request.method} {request.url}")
            raise RuntimeError("E2E: сетевой запрос к Telegram заблокирован")
        return await orig_httpx(self, request, *args, **kwargs)

    httpx.AsyncClient.send = guarded_httpx

    import services.answer as answer_mod

    orig_embed = answer_mod.embed_query
    last = [0.0]

    async def paced_embed(text: str):
        wait = VOYAGE_INTERVAL - (time.monotonic() - last[0])
        if wait > 0:
            await asyncio.sleep(wait)
        last[0] = time.monotonic()
        Stats.voyage_calls += 1
        return await orig_embed(text)

    answer_mod.embed_query = paced_embed


# --- проверка HTML, как это делает Telegram -------------------------------------------

ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "a", "code", "pre",
                "blockquote", "tg-spoiler", "span", "tg-emoji"}
ENTITY_RE = re.compile(r"&(?!(lt|gt|amp|quot|#\d+|#x[0-9a-fA-F]+);)")


class _TgHtmlChecker(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack: list[str] = []
        self.error: Optional[str] = None

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED_TAGS and not self.error:
            self.error = f"Unsupported start tag \"{tag}\""
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.error = self.error or f"Unexpected end tag \"{tag}\""
        else:
            self.stack.pop()


def telegram_html_error(text: str) -> Optional[str]:
    if ENTITY_RE.search(text):
        return "Unsupported HTML entity / bare '&'"
    p = _TgHtmlChecker()
    try:
        p.feed(text)
        p.close()
    except Exception as e:
        return f"parse error {e}"
    if p.error:
        return p.error
    if p.stack:
        return f"Unclosed tag \"{p.stack[-1]}\""
    return None


def plain_of(html: str) -> str:
    return html_lib.unescape(re.sub(r"<[^>]+>", "", html))


def markup_of(rm) -> Optional[dict]:
    if rm is None:
        return None
    if isinstance(rm, InlineKeyboardMarkup):
        return {"inline": [[b.text for b in row] for row in rm.inline_keyboard]}
    if isinstance(rm, ReplyKeyboardMarkup):
        return {"reply": [[b.text + (" [контакт]" if b.request_contact else "") for b in row] for row in rm.keyboard]}
    if isinstance(rm, ReplyKeyboardRemove):
        return {"remove_keyboard": True}
    return {"other": type(rm).__name__}


# --- фейковая сессия Bot API ---------------------------------------------------------

class FakeSession(BaseSession):
    """Все методы Bot API заканчиваются здесь. Ничего не уходит в сеть."""

    def __init__(self, sim: "Simulator"):
        super().__init__()
        self.sim = sim
        self._next_id = 1000

    async def close(self) -> None:
        pass

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        raise RuntimeError("E2E: stream_content не поддерживается")
        yield b""

    def _new_message(self, bot: Bot, chat_id: int, text: str, rm) -> Message:
        self._next_id += 1
        chat_type = "private" if chat_id > 0 or ID_MIN <= chat_id <= ID_MAX else "supergroup"
        chat = Chat(id=chat_id, type=chat_type, title=None if chat_type == "private" else "group")
        msg = Message(
            message_id=self._next_id,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=BOT_USER,
            text=text,
            reply_markup=rm if isinstance(rm, InlineKeyboardMarkup) else None,
        )
        return msg.as_(bot)

    async def make_request(self, bot: Bot, method, timeout: Optional[int] = None) -> Any:
        sim = self.sim
        name = type(method).__name__
        if isinstance(method, (SendChatAction, DeleteWebhook)):
            return True
        if isinstance(method, AnswerCallbackQuery):
            sim.record({"api": "answer_callback", "text": method.text, "show_alert": method.show_alert})
            return True
        if isinstance(method, (SendMessage, EditMessageText)):
            parse_mode = bot.default.parse_mode if isinstance(method.parse_mode, Default) else method.parse_mode
            err = None
            if parse_mode and str(parse_mode).upper() == "HTML":
                err = telegram_html_error(method.text)
            if len(plain_of(method.text) if parse_mode else method.text) > 4096:
                err = "message is too long"
            if not method.text.strip():
                err = "message text is empty"
            api = "send_message" if isinstance(method, SendMessage) else "edit_message"
            chat_id = int(method.chat_id) if method.chat_id is not None else None
            entry = {
                "api": api,
                "target": sim.target_of(chat_id),
                "chat_id": chat_id,
                "parse_mode": parse_mode,
                "text_raw": method.text,
                "text": plain_of(method.text) if parse_mode else method.text,
                "buttons": markup_of(method.reply_markup),
            }
            if isinstance(method, SendMessage):
                rp = method.reply_parameters
                if rp is not None and not isinstance(rp, Default):
                    entry["reply_to"] = rp.message_id
                elif isinstance(getattr(method, "reply_to_message_id", None), int):
                    entry["reply_to"] = method.reply_to_message_id
            if (isinstance(method, SendMessage) and entry.get("reply_to") and entry["target"] in ("LEADS_GROUP", "REPORTS_GROUP")
                    and (chat_id, entry["reply_to"]) not in sim.messages):
                # как настоящий Telegram: ответ на удалённое / несуществующее сообщение группы
                entry["telegram_error"] = "Bad Request: message to be replied not found"
                sim.record(entry)
                raise TelegramBadRequest(method=method, message=entry["telegram_error"])
            if err:
                entry["telegram_error"] = f"Bad Request: can't parse entities: {err}"
                sim.record(entry)
                raise TelegramBadRequest(method=method, message=entry["telegram_error"])
            sim.record(entry)
            if isinstance(method, SendMessage):
                msg = self._new_message(bot, chat_id, method.text, method.reply_markup)
                sim.remember(msg)
                return msg
            return sim.edit(bot, chat_id, method.message_id, text=method.text, rm=method.reply_markup)
        if isinstance(method, SendDocument):
            # «📄 Материалы» (Э5): фиксируем имя файла, подпись и отправку по file_id; file_id — с префиксом E2E_
            parse_mode = bot.default.parse_mode if isinstance(method.parse_mode, Default) else method.parse_mode
            chat_id = int(method.chat_id)
            doc = method.document
            by_file_id = isinstance(doc, str)
            filename = None if by_file_id else getattr(doc, "filename", None)
            caption = method.caption or ""
            entry = {"api": "send_document", "target": sim.target_of(chat_id), "chat_id": chat_id,
                     "parse_mode": parse_mode, "by_file_id": by_file_id,
                     "file_id": doc if by_file_id else None, "filename": filename,
                     "text_raw": caption, "text": plain_of(caption) if parse_mode else caption,
                     "buttons": markup_of(method.reply_markup)}
            err = telegram_html_error(caption) if parse_mode and str(parse_mode).upper() == "HTML" and caption else None
            if by_file_id and not str(doc).startswith("E2E_"):
                err = err or "wrong file identifier/HTTP URL specified"  # чужой file_id в симуляторе не существует
            if err:
                entry["telegram_error"] = f"Bad Request: {err}"
                sim.record(entry)
                raise TelegramBadRequest(method=method, message=entry["telegram_error"])
            sim.record(entry)
            msg = self._new_message(bot, chat_id, caption or "·", method.reply_markup)
            fid = doc if by_file_id else f"E2E_FILE_{self._next_id}"
            msg = msg.model_copy(update={"document": Document(file_id=fid, file_unique_id=f"e2e{self._next_id}",
                                                              file_name=filename)}).as_(bot)
            sim.remember(msg)
            return msg
        if isinstance(method, EditMessageReplyMarkup):
            chat_id = int(method.chat_id)
            sim.record({"api": "edit_markup", "target": sim.target_of(chat_id), "chat_id": chat_id,
                        "buttons": markup_of(method.reply_markup)})
            return sim.edit(bot, chat_id, method.message_id, rm=method.reply_markup, keep_text=True)
        sim.record({"api": name, "unsupported": True, "args": str(method)[:300]})
        raise RuntimeError(f"E2E: метод {name} не поддержан симулятором")


# --- захват логов бота -------------------------------------------------------------

class CaptureHandler(logging.Handler):
    def __init__(self, sim: "Simulator"):
        super().__init__(level=logging.WARNING)
        self.sim = sim

    def emit(self, record):
        if record.name.startswith("e2e"):
            return
        text = record.getMessage()
        if record.exc_info:
            text += "\n" + "".join(traceback.format_exception(*record.exc_info))[-1500:]
        self.sim.record({"api": "log", "level": record.levelname, "logger": record.name, "text": text})


# --- симулятор -----------------------------------------------------------------------

class Simulator:
    def __init__(self):
        self.session = FakeSession(self)
        self.bot = Bot(token=FAKE_TOKEN, session=self.session,
                       default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.dp = build_dispatcher()
        self.messages: dict[tuple[int, int], Message] = {}  # (chat_id, message_id) → сообщение бота
        self.user_msg_id = 1
        self.current: Optional[list] = None
        self.leads_chat: Optional[int] = None
        self.reports_chat: Optional[int] = None

    # учёт исходящих
    def record(self, entry: dict) -> None:
        if self.current is not None:
            self.current.append(entry)

    def target_of(self, chat_id: Optional[int]) -> str:
        if chat_id is None:
            return "?"
        if ID_MIN <= chat_id <= ID_MAX:
            return "user"
        if self.leads_chat is not None and chat_id == self.leads_chat:
            return "LEADS_GROUP"
        if self.reports_chat is not None and chat_id == self.reports_chat:
            return "REPORTS_GROUP"
        if chat_id == config.ADMIN_TELEGRAM_ID:
            return "ADMIN"
        return "FOREIGN_CHAT"

    def remember(self, msg: Message) -> None:
        self.messages[(msg.chat.id, msg.message_id)] = msg

    def edit(self, bot, chat_id, message_id, text=None, rm=None, keep_text=False):
        old = self.messages.get((chat_id, message_id))
        if old is None:
            return True
        new = old.model_copy(update={
            "text": old.text if keep_text else text,
            "reply_markup": rm if isinstance(rm, InlineKeyboardMarkup) else None,
        }).as_(bot)
        self.messages[(chat_id, message_id)] = new
        return new

    def find_button(self, uid: int, label: str):
        msgs = sorted((m for (c, _), m in self.messages.items() if c == uid), key=lambda m: -m.message_id)
        for match in (lambda t: t == label, lambda t: t.strip().lower() == label.strip().lower(),
                      lambda t: label.strip().lower() in t.lower()):
            for m in msgs:
                if not m.reply_markup:
                    continue
                for row in m.reply_markup.inline_keyboard:
                    for b in row:
                        if match(b.text):
                            return m, b
        return None, None

    # входящие
    def _user(self, uid: int, sc: dict) -> User:
        return User(id=uid, is_bot=False, first_name=sc.get("first_name", f"Тест{abs(uid)}"),
                    username=sc.get("username", f"e2e_{abs(uid)}"), language_code="ru")

    def _chat(self, uid: int, sc: dict) -> Chat:
        return Chat(id=uid, type="private", first_name=sc.get("first_name", f"Тест{abs(uid)}"))

    async def feed(self, update: Update) -> None:
        await self.dp.feed_update(self.bot, update)

    async def step(self, uid: int, sc: dict, step: dict) -> dict:
        out: list = []
        self.current = out
        action: dict = {}
        t0 = time.monotonic()
        try:
            self.user_msg_id += 1
            if "start" in step or "send" in step:
                text = ("/start" + (f" {step['start']}" if step.get("start") else "")) if "start" in step else step["send"]
                action = {"type": "message", "text": text}
                msg = Message(message_id=self.user_msg_id, date=datetime.now(timezone.utc), chat=self._chat(uid, sc),
                              from_user=self._user(uid, sc), text=text)
                await self.feed(Update(update_id=self.user_msg_id, message=msg))
            elif "contact" in step:
                action = {"type": "contact", "phone": step["contact"]}
                msg = Message(message_id=self.user_msg_id, date=datetime.now(timezone.utc), chat=self._chat(uid, sc),
                              from_user=self._user(uid, sc),
                              contact=Contact(phone_number=step["contact"], first_name=sc.get("first_name", "Тест"),
                                              user_id=uid))
                await self.feed(Update(update_id=self.user_msg_id, message=msg))
            elif "click" in step:
                m, b = self.find_button(uid, step["click"])
                action = {"type": "click", "button": step["click"]}
                if not b:
                    action["error"] = "кнопка не найдена среди inline-кнопок бота"
                else:
                    action["matched"] = b.text
                    cq = CallbackQuery(id=f"e2e{self.user_msg_id}", from_user=self._user(uid, sc),
                                       chat_instance="e2e", message=m, data=b.callback_data)
                    await self.feed(Update(update_id=self.user_msg_id, callback_query=cq))
            elif "preset_questions_used" in step:
                from services.limits import shanghai_today
                n = int(step["preset_questions_used"])
                await database.ensure_user(uid, sc.get("username", f"e2e_{abs(uid)}"), sc.get("first_name"))
                async with database.pool().acquire() as conn:
                    await conn.execute(
                        "UPDATE users SET questions_today=$2, questions_date=$3 WHERE telegram_id=$1",
                        uid, n, shanghai_today())
                action = {"type": "preset", "questions_used": n}
            elif "guard_probe" in step:
                # Контрольная проба: прямые запросы к api.telegram.org должны быть заблокированы предохранителем
                import aiohttp
                import httpx
                url = f"https://api.telegram.org/bot{FAKE_TOKEN}/getMe"
                blocked = []
                n_before = len(Stats.telegram_network_attempts)
                try:
                    async with aiohttp.ClientSession() as s:
                        await s.get(url)
                except RuntimeError as e:
                    blocked.append(f"aiohttp: {e}")
                try:
                    async with httpx.AsyncClient() as c:
                        await c.get(url)
                except RuntimeError as e:
                    blocked.append(f"httpx: {e}")
                del Stats.telegram_network_attempts[n_before:]  # пробы — не нарушения бота
                action = {"type": "guard_probe", "blocked": blocked, "ok": len(blocked) == 2}
            elif "delete_group_messages" in step:
                # «Иван удалил карточки в группе»: симулятор забывает сообщения бота в этой группе
                chat = self.leads_chat if step["delete_group_messages"] == "leads" else self.reports_chat
                gone = [k for k in self.messages if k[0] == chat]
                for k in gone:
                    del self.messages[k]
                action = {"type": "delete_group_messages", "group": step["delete_group_messages"], "deleted": len(gone)}
            elif "note" in step:
                action = {"type": "note", "text": step["note"]}
            else:
                action = {"type": "unknown", "step": step}
        except Exception as e:
            out.append({"api": "simulator_exception", "text": f"{type(e).__name__}: {e}",
                        "trace": traceback.format_exc()[-2000:]})
        finally:
            self.current = None
        await mark_test(uid)
        return {"action": action, "expect": step.get("expect"), "outputs": out,
                "seconds": round(time.monotonic() - t0, 1)}


def build_dispatcher() -> Dispatcher:
    """Тот же диспетчер, что у рабочего бота (main.build_dispatcher) — без отдельного списка роутеров."""
    from main import build_dispatcher as build_main

    return build_main()


# --- БД: метка и очистка тестовых пользователей -----------------------------------------

async def mark_test(uid: int) -> None:
    assert ID_MIN <= uid <= ID_MAX
    async with database.pool().acquire() as conn:
        await conn.execute("UPDATE users SET source='test' WHERE telegram_id=$1 AND source IS NULL", uid)


CLEAN_TABLES = ("messages", "leads", "events", "unanswered_questions", "tickets", "users")


async def cleanup() -> dict:
    counts = {}
    async with database.pool().acquire() as conn:
        async with conn.transaction():
            for t in CLEAN_TABLES:
                r = await conn.execute(f"DELETE FROM {t} WHERE telegram_id BETWEEN $1 AND $2", ID_MIN, ID_MAX)
                counts[t] = int(r.split()[-1])
        left = {t: await conn.fetchval(f"SELECT count(*) FROM {t} WHERE telegram_id BETWEEN $1 AND $2", ID_MIN, ID_MAX)
                for t in CLEAN_TABLES}
        # file_id из симулятора (E2E_…) в рабочей files_library не оставляем
        r = await conn.execute("UPDATE files_library SET tg_file_id = NULL WHERE tg_file_id LIKE 'E2E\\_%'")
        counts["files_library_e2e_file_id"] = int(r.split()[-1])
        left["files_library_e2e_file_id"] = await conn.fetchval(
            "SELECT count(*) FROM files_library WHERE tg_file_id LIKE 'E2E\\_%'")
    return {"deleted": counts, "left_after": left}


async def reset_user(sim: Simulator, uid: int) -> None:
    assert ID_MIN <= uid <= ID_MAX
    async with database.pool().acquire() as conn:
        async with conn.transaction():
            for t in CLEAN_TABLES:
                await conn.execute(f"DELETE FROM {t} WHERE telegram_id=$1", uid)
    from aiogram.fsm.storage.base import StorageKey
    key = StorageKey(bot_id=sim.bot.id, chat_id=uid, user_id=uid)
    await sim.dp.storage.set_state(key, None)
    await sim.dp.storage.set_data(key, {})


# --- отчёт прогона ------------------------------------------------------------------

def render_md(results: list, summary: dict) -> str:
    lines = [f"# E2E-прогон {summary['started']}", "",
             "```json", json.dumps(summary, ensure_ascii=False, indent=2), "```", ""]
    for r in results:
        lines += [f"## {r['id']}. {r.get('title', '')}", f"Пользователь {r['user']}", ""]
        for n, s in enumerate(r["steps"], 1):
            a = s["action"]
            desc = (a.get("text") or a.get("button") or a.get("phone") or a.get("questions_used")
                    or (a.get("blocked") if a.get("type") == "guard_probe" else "") or "")
            if isinstance(desc, str) and len(desc) > 300:
                desc = desc[:300] + f"… [{len(a.get('text', ''))} симв.]"
            lines.append(f"**{n}. ▶ {a.get('type')}:** `{desc}`" + (f" — ⚠️ {a['error']}" if a.get("error") else "")
                         + f" ({s['seconds']} с)")
            if s.get("expect"):
                lines.append(f"   _ожидание:_ {s['expect']}")
            for o in s["outputs"]:
                api = o["api"]
                if api in ("send_message", "edit_message"):
                    lines.append(f"- ◀ **{api} → {o['target']}**" + (f" (reply_to {o['reply_to']})" if o.get("reply_to") else "")
                                 + (f" ❌ {o['telegram_error']}" if o.get("telegram_error") else "")
                                 + f" · слов: {len(o['text'].split())}")
                    lines.append("  > " + o["text"].replace("\n", "\n  > "))
                    if o.get("buttons"):
                        lines.append(f"  кнопки: `{json.dumps(o['buttons'], ensure_ascii=False)}`")
                elif api == "edit_markup":
                    lines.append(f"- ◀ edit_markup → {o['target']}: `{json.dumps(o['buttons'], ensure_ascii=False)}`")
                elif api == "answer_callback":
                    lines.append(f"- ◀ answer_callback" + (f": «{o['text']}»" if o.get("text") else ""))
                else:
                    lines.append(f"- ⚠️ **{api}** {o.get('level', '')} {o.get('logger', '')}: "
                                 + str(o.get("text", o))[:1500].replace("\n", " ⏎ "))
            lines.append("")
    return "\n".join(lines)


async def run(scenarios_path: str, out_dir: str) -> int:
    data = json.loads(Path(scenarios_path).read_text())
    scenarios = data["scenarios"] if isinstance(data, dict) else data
    for sc in scenarios:
        assert ID_MIN <= int(sc["user"]) <= ID_MAX, f"{sc['id']}: user вне диапазона {ID_MIN}…{ID_MAX}"

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    install_patches()
    await database.init_pool()
    started = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    t0 = time.monotonic()
    pre = await cleanup()
    sim = Simulator()
    from services.leads import leads_chat_id
    sim.leads_chat = await leads_chat_id()
    from services.notify import chat_id_for
    sim.reports_chat = await chat_id_for("reports")
    logging.getLogger().addHandler(CaptureHandler(sim))

    results = []
    jsonl = (out / "transcript.jsonl").open("w")
    try:
        for sc in scenarios:
            uid = int(sc["user"])
            if sc.get("fresh"):
                await reset_user(sim, uid)
            print(f"[{time.strftime('%H:%M:%S')}] {sc['id']} {sc.get('title', '')}", flush=True)
            res = {"id": sc["id"], "title": sc.get("title", ""), "user": uid, "steps": []}
            for st in sc["steps"]:
                res["steps"].append(await sim.step(uid, sc, st))
            results.append(res)
            jsonl.write(json.dumps(res, ensure_ascii=False) + "\n")
            jsonl.flush()
    finally:
        jsonl.close()
        post = await cleanup() if not data.get("keep_users") else {"skipped": True}
        all_out = [o for r in results for s in r["steps"] for o in s["outputs"]]
        summary = {
            "started": started,
            "seconds": round(time.monotonic() - t0),
            "model": config.CLAUDE_MODEL,
            "scenarios": len(results),
            "steps": sum(len(r["steps"]) for r in results),
            "claude_calls": Stats.claude_calls,
            "tokens": {"in": Stats.tokens_in, "out": Stats.tokens_out, "cache_read": Stats.cache_read,
                       "cache_write": Stats.cache_write},
            "cost_usd_estimate": round(Stats.cost(), 3),
            "price_per_mtok": {"in": PRICE_IN, "out": PRICE_OUT, "cache_read": PRICE_CACHE_READ,
                               "cache_write": PRICE_CACHE_WRITE},
            "voyage_calls": Stats.voyage_calls,
            "guard": {
                "session": type(sim.bot.session).__name__,
                "token_is_fake": sim.bot.token == FAKE_TOKEN,
                "telegram_network_attempts": Stats.telegram_network_attempts,
                "intercepted_to_leads_group": sum(1 for o in all_out if o.get("target") == "LEADS_GROUP"),
                "intercepted_to_reports_group": sum(1 for o in all_out if o.get("target") == "REPORTS_GROUP"),
                "intercepted_to_admin": sum(1 for o in all_out if o.get("target") == "ADMIN"),
                "intercepted_to_foreign_chat": sum(1 for o in all_out if o.get("target") == "FOREIGN_CHAT"),
            },
            "warnings_in_logs": sum(1 for o in all_out if o["api"] == "log"),
            "telegram_errors": sum(1 for o in all_out if o.get("telegram_error")),
            "simulator_exceptions": sum(1 for o in all_out if o["api"] == "simulator_exception"),
            "cleanup_before": pre,
            "cleanup_after": post,
        }
        (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        (out / "transcript.md").write_text(render_md(results, summary))
        await database.close_pool()
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


async def cleanup_only() -> None:
    await database.init_pool()
    print(json.dumps(await cleanup(), ensure_ascii=False))
    await database.close_pool()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if len(sys.argv) > 1 and sys.argv[1] == "--cleanup":
        asyncio.run(cleanup_only())
    elif len(sys.argv) == 3:
        sys.exit(asyncio.run(run(sys.argv[1], sys.argv[2])))
    else:
        print(__doc__)
        sys.exit(2)
