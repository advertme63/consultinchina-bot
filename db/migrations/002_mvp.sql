-- ТЗ v2.1, раздел 9. Только добавляет таблицы и поля, существующие данные не трогает.
-- Применение: docker exec -i cinc_db psql -U cinc -d cinc -v ON_ERROR_STOP=1 -1 -f - < db/migrations/002_mvp.sql
-- Повторный запуск безопасен (IF NOT EXISTS).

-- users
ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS source TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS questions_today INT NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS questions_date DATE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;

-- documents: замена версий по doc_key, пропуск по хешу
ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_key TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_hash TEXT;
DO $$ BEGIN
    ALTER TABLE documents ADD CONSTRAINT documents_doc_key_key UNIQUE (doc_key);
EXCEPTION WHEN duplicate_table OR duplicate_object THEN NULL; END $$;

-- messages: вопросы и ответы с метаданными, хранение 12 месяцев
CREATE TABLE IF NOT EXISTS messages (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL REFERENCES users(telegram_id),
    question TEXT NOT NULL,
    answer TEXT,
    doc_keys TEXT[],
    best_distance REAL,
    answered_from_kb BOOL,
    intent TEXT,
    cta TEXT,
    tokens_in INT,
    tokens_out INT,
    latency_ms INT,
    rating SMALLINT CHECK (rating IN (1, -1)),
    feedback TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS messages_user_created_idx ON messages (telegram_id, created_at DESC);
CREATE INDEX IF NOT EXISTS messages_created_idx ON messages (created_at);

-- events: воронка и статистика (start / qualify_start / qualify_done / lead / limit_hit)
CREATE TABLE IF NOT EXISTS events (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT,
    type TEXT NOT NULL,
    payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS events_type_created_idx ON events (type, created_at);

-- leads: заявки менеджеру
CREATE TABLE IF NOT EXISTS leads (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL REFERENCES users(telegram_id),
    name TEXT,
    phone TEXT,
    answers JSONB,
    verdict TEXT,
    summary TEXT,
    status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'in_progress', 'closed')),
    manager_id BIGINT,
    group_message_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS leads_created_idx ON leads (created_at);

-- unanswered_questions: пробелы в базе для ежедневной сводки
CREATE TABLE IF NOT EXISTS unanswered_questions (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT,
    question TEXT NOT NULL,
    bot_answer TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    in_digest BOOL NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS unanswered_digest_idx ON unanswered_questions (in_digest, created_at);

-- settings: leads_chat_id и т.п.
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
