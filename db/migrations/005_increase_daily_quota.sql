-- =====================================================
-- Migration 005: subir cuota diaria de 5 a 10 shorts/dia
--
-- Cambios:
--   - stories_per_day:    5 -> 10  (WF1 generara 10 historias en lugar de 5)
--   - max_shorts_per_day: 5 -> 10  (WF3 producira hasta 10 shorts/dia)
--   - max_uploads_per_day: 5 -> 10 (WF4 subira hasta 10 a YouTube/dia)
--
-- Tras aplicar, recordar tambien:
--   - WF3 cron: cambiado de cada 4h a cada 2h (12 disparos/dia)
--   - WF4 cron: cambiado de cada 6h a cada 2h (12 disparos/dia)
--   - n8n se reimporta o se editan los crons en la UI
--
-- Aplicar:
--   docker compose exec -T postgres psql -U horror -d horror_shorts \
--     < db/migrations/005_increase_daily_quota.sql
-- =====================================================

SET search_path TO app, public;

UPDATE policy_params
SET value = '10'::jsonb,
    updated_at = now()
WHERE key IN ('stories_per_day', 'max_shorts_per_day', 'max_uploads_per_day');

-- Si alguna fila no existia, insertarla
INSERT INTO policy_params (key, value)
SELECT 'stories_per_day', '10'::jsonb
WHERE NOT EXISTS (SELECT 1 FROM policy_params WHERE key = 'stories_per_day');

INSERT INTO policy_params (key, value)
SELECT 'max_shorts_per_day', '10'::jsonb
WHERE NOT EXISTS (SELECT 1 FROM policy_params WHERE key = 'max_shorts_per_day');

INSERT INTO policy_params (key, value)
SELECT 'max_uploads_per_day', '10'::jsonb
WHERE NOT EXISTS (SELECT 1 FROM policy_params WHERE key = 'max_uploads_per_day');

-- Mostrar resultado
SELECT key, value, updated_at
FROM policy_params
WHERE key IN ('stories_per_day', 'max_shorts_per_day', 'max_uploads_per_day')
ORDER BY key;
