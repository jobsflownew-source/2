-- =====================================================
-- Migration 004: anadir limite diario de uploads a YouTube
--
-- Aplicar:
--   docker compose exec -T postgres psql -U horror -d horror_shorts \
--     < db/migrations/004_max_uploads_per_day.sql
-- =====================================================

SET search_path TO app, public;

INSERT INTO policy_params (key, value)
VALUES ('max_uploads_per_day', '5'::jsonb)
ON CONFLICT (key) DO NOTHING;

SELECT key, value FROM policy_params WHERE key='max_uploads_per_day';
