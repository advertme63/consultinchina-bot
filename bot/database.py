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

async def ensure_user(telegram_id: int, username: Optional[str], first_name: Optional[str] = None) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (telegram_id, username, first_name, last_seen_at)
            VALUES ($1, $2, $3, now())
            ON CONFLICT (telegram_id) DO UPDATE SET
                username = EXCLUDED.username,
                first_name = COALESCE(EXCLUDED.first_name, users.first_name),
                last_seen_at = now()
            """,
            telegram_id,
            username,
            first_name,
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


# --- hybrid search (ТЗ 3.4) ------------------------------------------------

async def search_hybrid(
    query_embedding: Optional[list[float]],
    query_text: str,
    limit: int = 8,
    pool_size: int = 20,
    fts_weight: float = 2.0,
) -> list[asyncpg.Record]:
    """Вектор + полнотекст (russian, слова через ИЛИ), объединение рангов RRF (k=60).
    Полнотекстовый ранг весит fts_weight: на длинных вопросах вектор voyage-3-lite уводит
    к общим фрагментам (тест 1 Э2: нужный фрагмент — 19-й по вектору, 1-й по тексту).
    query_embedding=None — только полнотекст (если Voyage недоступен).
    Поля: id, doc_key, content, distance, vrank, frank, matched (совпавших слов запроса), n_lex, score."""
    emb = _embedding_to_pg(query_embedding) if query_embedding else None
    async with pool().acquire() as conn:
        return await conn.fetch(
            """
            WITH q AS (
                SELECT replace(plainto_tsquery('russian', $2)::text, ' & ', ' | ') AS qs,
                       ARRAY(SELECT lexeme FROM unnest(to_tsvector('russian', $2))) AS lex
            ),
            vec AS (
                SELECT c.id, row_number() OVER (ORDER BY c.embedding <=> $1::vector) AS rnk
                FROM chunks c
                WHERE $1::vector IS NOT NULL
                ORDER BY c.embedding <=> $1::vector
                LIMIT $4
            ),
            fts AS (
                SELECT c.id,
                       cardinality(ARRAY(SELECT unnest(tsvector_to_array(c.tsv)) INTERSECT SELECT unnest(q.lex))) AS matched,
                       row_number() OVER (ORDER BY ts_rank_cd(c.tsv, q.qs::tsquery) DESC) AS rnk
                FROM chunks c, q
                WHERE q.qs <> '' AND c.tsv @@ q.qs::tsquery
                ORDER BY rnk
                LIMIT $4
            )
            SELECT c.id, d.doc_key, c.content,
                   CASE WHEN $1::vector IS NULL THEN NULL ELSE c.embedding <=> $1::vector END AS distance,
                   v.rnk AS vrank, f.rnk AS frank, COALESCE(f.matched, 0) AS matched,
                   (SELECT cardinality(lex) FROM q) AS n_lex,
                   COALESCE(1.0 / (60 + v.rnk), 0) + COALESCE($5::float / (60 + f.rnk), 0) AS score
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            LEFT JOIN vec v ON v.id = c.id
            LEFT JOIN fts f ON f.id = c.id
            WHERE v.id IS NOT NULL OR f.id IS NOT NULL
            ORDER BY score DESC, distance NULLS LAST
            LIMIT $3
            """,
            emb,
            query_text,
            limit,
            pool_size,
            fts_weight,
        )


async def best_vector_distance(query_embedding: list[float]) -> Optional[float]:
    async with pool().acquire() as conn:
        return await conn.fetchval(
            "SELECT min(embedding <=> $1::vector) FROM chunks", _embedding_to_pg(query_embedding)
        )


# --- daily limits (ТЗ 2) ---------------------------------------------------

async def take_question(telegram_id: int, today: date, limit: int) -> Optional[int]:
    """Списывает один вопрос из суточного лимита. Возвращает число использованных
    за сегодня или None, если лимит исчерпан."""
    async with pool().acquire() as conn:
        return await conn.fetchval(
            """
            UPDATE users SET
                questions_today = CASE WHEN questions_date = $2 THEN questions_today + 1 ELSE 1 END,
                questions_date = $2
            WHERE telegram_id = $1 AND (questions_date IS DISTINCT FROM $2 OR questions_today < $3)
            RETURNING questions_today
            """,
            telegram_id,
            today,
            limit,
        )


async def refund_question(telegram_id: int, today: date) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            """
            UPDATE users SET questions_today = greatest(questions_today - 1, 0)
            WHERE telegram_id = $1 AND questions_date = $2
            """,
            telegram_id,
            today,
        )


async def questions_used(telegram_id: int, today: date) -> int:
    async with pool().acquire() as conn:
        used = await conn.fetchval(
            "SELECT CASE WHEN questions_date = $2 THEN questions_today ELSE 0 END FROM users WHERE telegram_id = $1",
            telegram_id,
            today,
        )
    return used or 0


# --- messages / events / unanswered -----------------------------------------

async def recent_dialog(telegram_id: int, pairs: int = 3) -> list[asyncpg.Record]:
    """Последние пары «вопрос — ответ» в хронологическом порядке."""
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT question, answer FROM messages
            WHERE telegram_id = $1 AND answer IS NOT NULL
            ORDER BY created_at DESC, id DESC LIMIT $2
            """,
            telegram_id,
            pairs,
        )
    return list(reversed(rows))


async def save_message(
    telegram_id: int,
    question: str,
    answer: str,
    doc_keys: list[str],
    best_distance: Optional[float],
    answered_from_kb: bool,
    intent: str,
    cta: str,
    tokens_in: int,
    tokens_out: int,
    latency_ms: int,
) -> int:
    async with pool().acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO messages (telegram_id, question, answer, doc_keys, best_distance, answered_from_kb,
                                  intent, cta, tokens_in, tokens_out, latency_ms)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) RETURNING id
            """,
            telegram_id,
            question,
            answer,
            doc_keys,
            best_distance,
            answered_from_kb,
            intent,
            cta,
            tokens_in,
            tokens_out,
            latency_ms,
        )


async def save_unanswered(telegram_id: int, question: str, bot_answer: str) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO unanswered_questions (telegram_id, question, bot_answer) VALUES ($1, $2, $3)",
            telegram_id,
            question,
            bot_answer,
        )


async def log_event(telegram_id: Optional[int], event_type: str, payload: Optional[str] = None) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO events (telegram_id, type, payload) VALUES ($1, $2, $3::jsonb)",
            telegram_id,
            event_type,
            payload,
        )
