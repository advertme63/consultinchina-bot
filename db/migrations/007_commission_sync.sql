-- Э7 «Автообновление комиссий»: FBW для WB, детали Ozon РФ (схемы), все пороги Ozon Китай (rFBS и FBP).
-- Даты последнего успешного обновления площадок — в settings (commissions:<площадка>:last_success).
-- Применение: docker exec -i cinc_db psql -U cinc -d cinc -v ON_ERROR_STOP=1 -1 -f - < db/migrations/007_commission_sync.sql
-- Повторный запуск безопасен. Откат: ALTER TABLE mp_commissions DROP COLUMN wb_fbw_pct, DROP COLUMN ozon_rf_detail, DROP COLUMN ozon_cn_tiers.
ALTER TABLE mp_commissions ADD COLUMN IF NOT EXISTS wb_fbw_pct NUMERIC(5,2);
ALTER TABLE mp_commissions ADD COLUMN IF NOT EXISTS ozon_rf_detail JSONB;
ALTER TABLE mp_commissions ADD COLUMN IF NOT EXISTS ozon_cn_tiers JSONB;
