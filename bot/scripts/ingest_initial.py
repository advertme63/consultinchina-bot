import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, "/app")

import database
from services.pdf_ingest import chunk_text, extract_text
from services.voyage_client import embed_documents

SOURCE_DIR = Path("/app/data/source_pdfs")

TAGS = {
    "ConsultInChina Возможности WFOE 2026.pdf": ("регистрация", "справочник"),
    "ConsultInChina_WFOE_Торговые_Возможности_2026.pdf": ("маркетплейсы", "справочник"),
    "ConsultInChina_Налоги_КНР_2026.pdf": ("бухгалтерия", "справочник"),
    "ConsultInChina_Трудоустройство_2026.pdf": ("визы", "справочник"),
    "Ликвидация_WFOE_справочник_2026_1.pdf": ("ликвидация", "справочник"),
}


async def main() -> None:
    await database.init_pool()
    try:
        for filename, (topic, doc_type) in TAGS.items():
            if await database.document_exists(filename):
                print(f"[skip] {filename} — already ingested")
                continue
            path = SOURCE_DIR / filename
            if not path.exists():
                print(f"[skip] {filename} not found at {path}")
                continue
            text = extract_text(str(path))
            chunks = chunk_text(text)
            if not chunks:
                print(f"[skip] {filename} — no text extracted")
                continue
            await asyncio.sleep(22)  # Voyage free-tier rate limit: 3 RPM without a payment method
            embeddings = await embed_documents(chunks)
            version_date = date.today()
            document_id = await database.create_document(filename, topic, doc_type, version_date, None)
            await database.insert_chunks(
                document_id, topic, doc_type, version_date, list(zip(chunks, embeddings))
            )
            print(f"[ok] {filename}: {len(chunks)} chunks, topic={topic}")
    finally:
        await database.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
