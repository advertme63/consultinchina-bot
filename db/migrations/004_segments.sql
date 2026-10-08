-- Э3.5 «Сегменты» + комиссии маркетплейсов. Только добавляет, существующие данные не удаляет.
-- Применение: docker exec -i cinc_db psql -U cinc -d cinc -v ON_ERROR_STOP=1 -1 -f - < db/migrations/004_segments.sql
-- Повторный запуск безопасен. Откат: DROP TABLE mp_commissions; ALTER TABLE users/leads/events DROP COLUMN segment;

ALTER TABLE users ADD COLUMN IF NOT EXISTS segment TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS segment TEXT;
ALTER TABLE events ADD COLUMN IF NOT EXISTS segment TEXT;

DO $$ BEGIN
    ALTER TABLE users ADD CONSTRAINT users_segment_check
        CHECK (segment IN ('seller', 'importer', 'expansion', 'service', 'other'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
    ALTER TABLE leads ADD CONSTRAINT leads_segment_check
        CHECK (segment IN ('seller', 'importer', 'expansion', 'service', 'other'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
    ALTER TABLE events ADD CONSTRAINT events_segment_check
        CHECK (segment IN ('seller', 'importer', 'expansion', 'service', 'other'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE INDEX IF NOT EXISTS events_segment_idx ON events (segment, type, created_at);

-- Старые записи: квалификация Э3 была только селлерская
UPDATE users SET segment = 'seller'
WHERE segment IS NULL AND telegram_id IN (SELECT telegram_id FROM events WHERE type = 'qualify_done');
UPDATE leads SET segment = 'seller' WHERE segment IS NULL AND verdict IS NOT NULL;
UPDATE events SET segment = 'seller' WHERE segment IS NULL AND type IN ('qualify_start', 'qualify_done');

-- Комиссии маркетплейсов: колонки как в mp_commissions.csv + position (порядок кнопок) + loaded_at
CREATE TABLE IF NOT EXISTS mp_commissions (
    button TEXT PRIMARY KEY,
    position INT NOT NULL,
    wb_category TEXT,
    wb_rf_pct NUMERIC(5, 2),
    wb_cn_pct NUMERIC(5, 2),
    wb_source TEXT,
    wb_date DATE,
    ozon_category TEXT,
    ozon_rf_pct NUMERIC(5, 2),
    ozon_cn_pct NUMERIC(5, 2),
    ozon_rf_source TEXT,
    ozon_rf_date DATE,
    ozon_cn_source TEXT,
    ozon_cn_date DATE,
    note TEXT,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
