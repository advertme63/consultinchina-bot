"""Загрузка документов в базу знаний с заменой версии по doc_key (ТЗ v2.1, раздел 3)."""
import asyncio
import hashlib
import logging
import os
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import voyageai.error

import database
from services.md_chunker import doc_title_from_filename, parse_markdown
from services.pdf_ingest import chunk_text, extract_text
from services.voyage_client import embed_documents_with_usage

logger = logging.getLogger(__name__)

KB_DIR = Path("/app/data/kb")
# Блоки «Как пользоваться (для бота)» — в поиск не идут, на Э2 собираются в системный промпт
RULES_DIR = Path("/app/data/kb_rules")

# Таблица 3.1 ТЗ + справочники Э3.5: doc_key → (файл, тема)
KB_SOURCES: dict[str, tuple[str, str]] = {
    "wfoe": ("Возможности_WFOE_2026.md", "регистрация"),
    "trade": ("Торговые_возможности_WFOE_2026.md", "маркетплейсы, ВЭД"),
    "tax": ("Налоги_КНР_2026.md", "бухгалтерия, налоги"),
    "liquidation": ("Ликвидация_WFOE_2026.md", "ликвидация"),
    "payments": ("Справочник_Оплата_поставщиков_и_фапьяо.md", "оплата поставщикам"),
    "employment": ("Трудоустройство_2026.md", "визы, найм"),
    "prices": ("Прайс_и_условия.md", "цены, оплата, второй год"),
    # Э3.5
    "suppliers": ("Поставщики_и_закупки_2026.md", "закупки, поставщики"),
    "logistics": ("Логистика_и_таможня_2026.md", "логистика, таможня"),
    "entity_choice": ("Выбор_формы_компании_2026.md", "регистрация, форма компании"),
    "accounting_switch": ("Переход_на_бухгалтерию_CinC.md", "бухгалтерия, переход"),
    "registration": ("Регистрация_компании_WFOE_2026.md", "регистрация"),
    # собирается scripts/load_commissions.py из таблицы mp_commissions
    "commissions": ("Комиссии_маркетплейсов.md", "комиссии маркетплейсов"),
}

# Бесплатный тариф Voyage без карты: 3 запроса и 10 000 токенов в минуту
VOYAGE_RPM = int(os.environ.get("VOYAGE_RPM", "3"))
VOYAGE_TPM = int(os.environ.get("VOYAGE_TPM", "10000"))


class _VoyageLimiter:
    def __init__(self) -> None:
        self._next_at = 0.0
        self._lock = asyncio.Lock()

    async def embed(self, texts: list[str]) -> tuple[list[list[float]], int]:
        async with self._lock:
            for attempt in range(4):
                wait = self._next_at - time.monotonic()
                if wait > 0:
                    logger.info("Voyage: пауза %.0f с под лимит", wait)
                    await asyncio.sleep(wait)
                try:
                    embeddings, tokens = await embed_documents_with_usage(texts)
                except voyageai.error.RateLimitError:
                    logger.warning("Voyage: лимит запросов, попытка %d, жду 65 с", attempt + 1)
                    self._next_at = time.monotonic() + 65
                    continue
                gap = max(60 / VOYAGE_RPM, 60 * tokens / VOYAGE_TPM) + 1
                self._next_at = time.monotonic() + gap
                return embeddings, tokens
            raise RuntimeError("Voyage: лимит запросов не отпускает после 4 попыток")


limiter = _VoyageLimiter()


@dataclass
class IngestResult:
    doc_key: str
    filename: str
    status: str  # loaded | unchanged
    chunks: int = 0
    tokens: int = 0
    replaced: bool = False


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_rules(doc_key: str, filename: str, rules: str) -> None:
    RULES_DIR.mkdir(parents=True, exist_ok=True)
    path = RULES_DIR / f"{doc_key}.md"
    if rules:
        path.write_text(f"## {doc_title_from_filename(filename)}\n\n{rules}\n", encoding="utf-8")
    elif path.exists():
        path.unlink()


async def ingest(
    doc_key: str,
    filename: str,
    topic: str,
    data: bytes,
    uploaded_by: Optional[int] = None,
    pdf_path: Optional[str] = None,
) -> IngestResult:
    """Загружает .md (или .pdf, если передан pdf_path). Тот же хеш → unchanged."""
    source_hash = file_hash(data)
    existing = await database.get_document_by_key(doc_key)
    if existing and existing["source_hash"] == source_hash:
        return IngestResult(doc_key, filename, "unchanged")

    if pdf_path:
        title = filename.rsplit(".", 1)[0]
        chunks = [f"{title}\n\n{c}" for c in chunk_text(extract_text(pdf_path))]
        version_date, doc_type, rules = date.today(), "справочник", ""
    else:
        parsed = parse_markdown(data.decode("utf-8-sig"), filename)
        chunks, rules = parsed.chunks, parsed.rules
        version_date = parsed.version_date or date.today()
        doc_type = parsed.doc_type
    if not chunks:
        raise ValueError(f"{filename}: не удалось получить ни одного фрагмента")

    embeddings, tokens = await limiter.embed(chunks)
    _, replaced = await database.replace_document(
        doc_key, filename, topic, doc_type, version_date, source_hash, uploaded_by,
        list(zip(chunks, embeddings)),
    )
    if not pdf_path:
        save_rules(doc_key, filename, rules)
    return IngestResult(doc_key, filename, "loaded", len(chunks), tokens, replaced)
