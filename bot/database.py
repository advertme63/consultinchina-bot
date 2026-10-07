from datetime import date, datetime
from typing import Optional

import asyncpg

from config import config

_pool: Optional[asyncpg.Pool] = None


async def init_pool() -> None:
    global _pool
    _pool = await asyncpg.create_pool(config.DATABASE_URL, min_size=1, max_size=5)


async def close_pool() -> None:
    if _pool:
        await _pool.close()


def pool() -> asyncpg.Pool:
    assert _pool is not None, "DB pool is not initialized"
    return _pool


def _embedding_to_pg(embedding: list[float]) -> str:
    return "[" + ",".join(str(x) for x in embedding) + "]"


# --- users -------------------------------------------------------------

async def ensure_user(telegram_id: int, username: Optional[str]) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (telegram_id, username)
            VALUES ($1, $2)
            ON CONFLICT (telegram_id) DO UPDATE SET username = EXCLUDED.username
            """,
            telegram_id,
            username,
        )


async def get_user(telegram_id: int) -> Optional[asyncpg.Record]:
    async with pool().acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE telegram_id = $1", telegram_id)


async def set_role(telegram_id: int, role: str, trial_expires_at: Optional[datetime] = None) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            """
            UPDATE users SET role = $2, trial_expires_at = $3, updated_at = now()
            WHERE telegram_id = $1
            """,
            telegram_id,
            role,
            trial_expires_at,
        )


async def list_active_access() -> list[asyncpg.Record]:
    async with pool().acquire() as conn:
        return await conn.fetch(
            """
            SELECT telegram_id, username, role, trial_expires_at
            FROM users WHERE role != 'none'
            ORDER BY updated_at DESC
            """
        )


# --- documents / chunks --------------------------------------------------

async def document_exists(filename: str) -> bool:
    async with pool().acquire() as conn:
        return await conn.fetchval("SELECT EXISTS(SELECT 1 FROM documents WHERE filename = $1)", filename)


async def create_document(
    filename: str, topic: str, doc_type: str, version_date: date, uploaded_by: Optional[int]
) -> int:
    async with pool().acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO documents (filename, topic, doc_type, version_date, uploaded_by)
            VALUES ($1, $2, $3, $4, $5) RETURNING id
            """,
            filename,
            topic,
            doc_type,
            version_date,
            uploaded_by,
        )


async def insert_chunks(
    document_id: int,
    topic: str,
    doc_type: str,
    version_date: date,
    chunks_with_embeddings: list[tuple[str, list[float]]],
) -> None:
    async with pool().acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO chunks (document_id, content, embedding, topic, doc_type, version_date)
            VALUES ($1, $2, $3::vector, $4, $5, $6)
            """,
            [
                (document_id, content, _embedding_to_pg(embedding), topic, doc_type, version_date)
                for content, embedding in chunks_with_embeddings
            ],
        )


async def get_document_by_key(doc_key: str) -> Optional[asyncpg.Record]:
    async with pool().acquire() as conn:
        return await conn.fetchrow("SELECT * FROM documents WHERE doc_key = $1", doc_key)


async def replace_document(
    doc_key: str,
    filename: str,
    topic: str,
    doc_type: str,
    version_date: date,
    source_hash: str,
    uploaded_by: Optional[int],
    chunks_with_embeddings: list[tuple[str, list[float]]],
) -> tuple[int, bool]:
    """Одна транзакция: удалить старую версию по doc_key (фрагменты каскадом) и записать новую.
    Возвращает (id нового документа, была ли заменена старая версия)."""
    async with pool().acquire() as conn:
        async with conn.transaction():
            old_id = await conn.fetchval("DELETE FROM documents WHERE doc_key = $1 RETURNING id", doc_key)
            document_id = await conn.fetchval(
                """
                INSERT INTO documents (filename, topic, doc_type, version_date, uploaded_by, doc_key, source_hash)
                VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id
                """,
                filename,
                topic,
                doc_type,
                version_date,
                uploaded_by,
                doc_key,
                source_hash,
            )
            await conn.executemany(
                """
                INSERT INTO chunks (document_id, content, embedding, topic, doc_type, version_date)
                VALUES ($1, $2, $3::vector, $4, $5, $6)
                """,
                [
                    (document_id, content, _embedding_to_pg(embedding), topic, doc_type, version_date)
                    for content, embedding in chunks_with_embeddings
                ],
            )
    return document_id, old_id is not None


async def list_documents() -> list[asyncpg.Record]:
    async with pool().acquire() as conn:
        return await conn.fetch(
            """
            SELECT d.id, d.doc_key, d.filename, d.topic, d.version_date, d.uploaded_at,
                   count(c.id) AS chunks
            FROM documents d LEFT JOIN chunks c ON c.document_id = d.id
            GROUP BY d.id
            ORDER BY d.doc_key NULLS LAST, d.id
            """
        )


async def delete_document_by_key(doc_key: str) -> Optional[str]:
    async with pool().acquire() as conn:
        return await conn.fetchval("DELETE FROM documents WHERE doc_key = $1 RETURNING filename", doc_key)


async def search_chunks(
    query_embedding: list[float], topic: Optional[str] = None, limit: int = 8
) -> list[asyncpg.Record]:
    embedding_literal = _embedding_to_pg(query_embedding)
    async with pool().acquire() as conn:
        if topic:
            rows = await conn.fetch(
                """
                SELECT content, topic, version_date, document_id,
                       embedding <=> $1::vector AS distance
                FROM chunks
                WHERE topic = $2
                ORDER BY embedding <=> $1::vector
                LIMIT $3
                """,
                embedding_literal,
                topic,
                limit,
            )
            if rows:
                return rows
        return await conn.fetch(
            """
            SELECT content, topic, version_date, document_id,
                   embedding <=> $1::vector AS distance
            FROM chunks
            ORDER BY embedding <=> $1::vector
            LIMIT $2
            """,
            embedding_literal,
            limit,
        )


# --- tickets -------------------------------------------------------------

async def create_ticket(telegram_id: int, category: str, text: str) -> int:
    async with pool().acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO tickets (telegram_id, category, text) VALUES ($1, $2, $3) RETURNING id",
            telegram_id,
            category,
            text,
        )


# --- files library ---------------------------------------------------------

async def add_file_to_library(filename: str, storage_path: str, title: str) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO files_library (filename, storage_path, title) VALUES ($1, $2, $3)",
            filename,
            storage_path,
            title,
        )


async def list_files_library() -> list[asyncpg.Record]:
    async with pool().acquire() as conn:
        return await conn.fetch("SELECT * FROM files_library ORDER BY uploaded_at DESC")
