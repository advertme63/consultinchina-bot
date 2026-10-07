"""Загрузка справочников из data/kb/ по таблице 3.1 ТЗ.

Запуск: docker compose exec bot python scripts/ingest_kb.py [--dry-run] [doc_key ...]
--dry-run — только нарезка и статистика, без Voyage и записи в базу.
"""
import asyncio
import logging
import sys

sys.path.insert(0, "/app")

import database
from services.kb_ingest import KB_DIR, KB_SOURCES, ingest
from services.md_chunker import parse_markdown

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")


async def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv
    keys = args or list(KB_SOURCES)
    unknown = [k for k in keys if k not in KB_SOURCES]
    if unknown:
        print(f"Неизвестные doc_key: {', '.join(unknown)}")
        return 2

    missing = [KB_SOURCES[k][0] for k in keys if not (KB_DIR / KB_SOURCES[k][0]).exists()]
    if missing:
        print("Нет файлов в data/kb/: " + ", ".join(missing))
        return 2

    if dry_run:
        for k in keys:
            filename, _ = KB_SOURCES[k]
            parsed = parse_markdown((KB_DIR / filename).read_text(encoding="utf-8-sig"), filename)
            sizes = [len(c) for c in parsed.chunks]
            print(f"{k:12} {len(sizes):3} фрагм., {min(sizes)}–{max(sizes)} симв., правила {len(parsed.rules)} симв.")
        return 0

    failed = 0
    await database.init_pool()
    try:
        for k in keys:
            filename, topic = KB_SOURCES[k]
            try:
                r = await ingest(k, filename, topic, (KB_DIR / filename).read_bytes())
            except Exception as e:
                failed += 1
                print(f"[ОШИБКА] {k}: {type(e).__name__}: {e}", flush=True)
                continue
            if r.status == "unchanged":
                print(f"[без изменений] {k} ({filename})", flush=True)
            else:
                action = "заменён" if r.replaced else "загружен"
                print(f"[{action}] {k} ({filename}): {r.chunks} фрагм., {r.tokens} токенов", flush=True)
    finally:
        await database.close_pool()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
