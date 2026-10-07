-- ТЗ v2.2, раздел 3.4: полнотекстовый поиск (russian) по фрагментам для гибридного поиска.
-- Применение: docker exec -i cinc_db psql -U cinc -d cinc -v ON_ERROR_STOP=1 -1 -f - < db/migrations/003_fts.sql
-- Откат: ALTER TABLE chunks DROP COLUMN tsv;
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('russian', content)) STORED;
CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING gin (tsv);
