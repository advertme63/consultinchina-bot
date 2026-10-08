-- Э5 «Материалы»: описание, порядок, чистое имя файла при отправке, кэш file_id Telegram.
-- Применение: docker exec -i cinc_db psql -U cinc -d cinc -v ON_ERROR_STOP=1 -1 -f - < db/migrations/006_materials.sql
-- Повторный запуск безопасен. Откат — из дампа pre_e5 (или DROP COLUMN).
ALTER TABLE files_library ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE files_library ADD COLUMN IF NOT EXISTS sort_order INT;
ALTER TABLE files_library ADD COLUMN IF NOT EXISTS send_name TEXT;
ALTER TABLE files_library ADD COLUMN IF NOT EXISTS tg_file_id TEXT;
-- Текущие 3 записи (решение Ивана 08.10)
UPDATE files_library SET sort_order = 1, title = 'Consult in China — о компании',
    description = 'Услуги, тарифы и как мы работаем', send_name = 'Consult_in_China_О_компании.pdf'
WHERE id = 1 AND filename = 'Общая.pdf';
UPDATE files_library SET sort_order = 2, title = 'Открытие компании в Китае (WFOE)',
    description = 'Тарифы 19 / 29 / 39 тыс. CNY, этапы, что входит', send_name = 'Consult_in_China_Открытие_компании_в_Китае.pdf'
WHERE id = 2 AND filename = 'Открытие компании.pdf';
UPDATE files_library SET sort_order = 3, title = 'Бизнес-тур Шанхай – Иу – Шанхай',
    description = '5 дней. О ближайших датах — у нашего специалиста', send_name = 'Consult_in_China_Бизнес-тур.pdf'
WHERE id = 3 AND filename = 'Бизнес тур.pdf';
