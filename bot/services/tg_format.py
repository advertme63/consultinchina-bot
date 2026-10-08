"""Ответ Claude → Telegram HTML (ТЗ 4.4): экранирование <, >, &, затем **жирный** и списки."""
import re
from html import escape

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.S)
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.M)
_BULLET_RE = re.compile(r"^(\s*)[-*]\s+", re.M)

TELEGRAM_LIMIT = 4000


def to_telegram_html(text: str, limit: int | None = TELEGRAM_LIMIT) -> str:
    """limit=None — без обрезки (длинные отчёты делит на части services/notify)."""
    html = escape(text, quote=False)
    html = _HEADING_RE.sub(r"<b>\1</b>", html)
    html = _BOLD_RE.sub(r"<b>\1</b>", html)
    html = _BULLET_RE.sub(r"\1• ", html)
    return html[:limit] if limit else html


def to_plain(text: str, limit: int | None = TELEGRAM_LIMIT) -> str:
    plain = _HEADING_RE.sub(r"\1", text)
    plain = _BOLD_RE.sub(r"\1", plain)
    plain = _BULLET_RE.sub(r"\1• ", plain)
    return plain[:limit] if limit else plain
