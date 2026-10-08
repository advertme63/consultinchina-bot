-- Э4 «Статистика и анализ» + кнопки под ответами. Только добавляет, существующие данные не удаляет.
-- Применение: docker exec -i cinc_db psql -U cinc -d cinc -v ON_ERROR_STOP=1 -1 -f - < db/migrations/005_e4.sql
-- Повторный запуск безопасен. Откат — из дампа pre_e4 (или DROP новых колонок / llm_usage).

-- messages: сегмент, раздельные токены кеша (tokens_in с Э4 — только «свежий» вход), кнопки
ALTER TABLE messages ADD COLUMN IF NOT EXISTS segment TEXT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS cache_read INT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS cache_write_5m INT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS cache_write_1h INT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS service TEXT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS suggestions TEXT[];

ALTER TABLE unanswered_questions ADD COLUMN IF NOT EXISTS segment TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS service TEXT;

DO $$ BEGIN
    ALTER TABLE messages ADD CONSTRAINT messages_segment_check
        CHECK (segment IN ('seller', 'importer', 'expansion', 'service', 'other'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
    ALTER TABLE unanswered_questions ADD CONSTRAINT unanswered_segment_check
        CHECK (segment IN ('seller', 'importer', 'expansion', 'service', 'other'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Бэкфилл сегмента: по текущему users.segment (точного сегмента на момент вопроса до Э4 нет)
UPDATE messages m SET segment = u.segment FROM users u
WHERE m.telegram_id = u.telegram_id AND m.segment IS NULL AND u.segment IS NOT NULL;
UPDATE unanswered_questions q SET segment = u.segment FROM users u
WHERE q.telegram_id = u.telegram_id AND q.segment IS NULL AND u.segment IS NOT NULL;

-- users: стабильный внутренний номер для /export (вместо telegram_id)
ALTER TABLE users ADD COLUMN IF NOT EXISTS export_no BIGSERIAL;
CREATE UNIQUE INDEX IF NOT EXISTS users_export_no_idx ON users (export_no);

-- Учёт ВСЕХ вызовов Claude (ответы, итоги, резюме, сводка, анализ — в т. ч. тесты и админ)
CREATE TABLE IF NOT EXISTS llm_usage (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    telegram_id BIGINT,
    purpose TEXT NOT NULL,
    model TEXT NOT NULL,
    input INT NOT NULL DEFAULT 0,
    cache_read INT NOT NULL DEFAULT 0,
    cache_write_5m INT NOT NULL DEFAULT 0,
    cache_write_1h INT NOT NULL DEFAULT 0,
    output INT NOT NULL DEFAULT 0,
    cost_usd NUMERIC(10, 6) NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS llm_usage_created_idx ON llm_usage (created_at);
CREATE INDEX IF NOT EXISTS messages_rated_idx ON messages (created_at) WHERE rating IS NOT NULL;
