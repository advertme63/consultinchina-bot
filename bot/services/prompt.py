"""Системный промпт из файлов (ТЗ 4.3): role + voice + rules + правила справочников + публичный прайс.
Файлы перечитываются, если изменились (по mtime) — правка промпта не требует пересборки кода."""
import re
from pathlib import Path

from services.kb_ingest import KB_DIR, KB_SOURCES, RULES_DIR

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
PROMPT_FILES = ["role.md", "voice.md", "rules.md"]

_BOT_BLOCK_RE = re.compile(r"^## [^\n]*для бота[^\n]*\n.*?(?=^## |\Z)", re.M | re.S | re.I)

_cache: dict = {"key": None, "text": ""}


def _strip_front_matter(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            return text[end + 4:].lstrip("\n")
    return text


def _sources() -> list[Path]:
    files = [PROMPTS_DIR / f for f in PROMPT_FILES]
    files += sorted(RULES_DIR.glob("*.md")) if RULES_DIR.exists() else []
    files.append(KB_DIR / KB_SOURCES["prices"][0])
    return files


def system_prompt() -> str:
    files = _sources()
    key = tuple((str(f), f.stat().st_mtime) for f in files if f.exists())
    if _cache["key"] == key:
        return _cache["text"]

    parts = [(PROMPTS_DIR / f).read_text(encoding="utf-8").strip() for f in PROMPT_FILES]

    rules = [f.read_text(encoding="utf-8").strip() for f in files if f.parent == RULES_DIR]
    if rules:
        parts.append("# Правила по темам справочников\n\n" + "\n\n".join(rules))

    prices_path = KB_DIR / KB_SOURCES["prices"][0]
    if prices_path.exists():
        prices = _BOT_BLOCK_RE.sub("", _strip_front_matter(prices_path.read_text(encoding="utf-8")))
        parts.append("# Публичный прайс (единственный источник цен)\n\n" + prices.strip())

    text = "\n\n---\n\n".join(parts)
    _cache.update(key=key, text=text)
    return text
