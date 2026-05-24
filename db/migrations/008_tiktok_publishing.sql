-- =====================================================
-- Migration 008: publicar shorts en TikTok ademas de YouTube
--
-- Anade columnas tiktok_* a shorts y nuevas policy_params
-- para controlar el limite diario y el modo de publicacion.
--
-- TikTok Content Posting API soporta 2 modos:
--   1. DIRECT_INBOX: video llega al inbox/drafts del creador.
--      Disponible sin app audit (scope video.upload).
--   2. DIRECT_POST: publica directamente en el feed publico.
--      Requiere app audit por TikTok (scope video.publish), tarda
--      1-2 semanas. Activa con policy_params.tiktok_publish_mode.
--
-- Aplicar:
--   docker compose exec -T postgres psql -U horror -d horror_shorts \
--     < db/migrations/008_tiktok_publishing.sql
-- =====================================================

SET search_path TO app, public;

-- 1. Columnas de tracking en shorts
ALTER TABLE shorts
    ADD COLUMN IF NOT EXISTS tiktok_video_id     TEXT,
    ADD COLUMN IF NOT EXISTS tiktok_publish_id   TEXT,
    ADD COLUMN IF NOT EXISTS tiktok_url          TEXT,
    ADD COLUMN IF NOT EXISTS tiktok_status       TEXT
        CHECK (tiktok_status IN ('pending','uploading','published',
                                 'failed','disabled') OR tiktok_status IS NULL),
    ADD COLUMN IF NOT EXISTS tiktok_published_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS tiktok_error        TEXT;

CREATE INDEX IF NOT EXISTS idx_shorts_tiktok_status
    ON shorts (tiktok_status)
    WHERE tiktok_status IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_shorts_tiktok_pending
    ON shorts (created_at)
    WHERE status = 'rendered'
      AND (tiktok_status IS NULL OR tiktok_status IN ('pending','failed'));

-- 2. policy_params para TikTok
INSERT INTO policy_params (key, value) VALUES
    ('max_tiktok_uploads_per_day', '10'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- 'inbox' = DIRECT_INBOX (no audit), 'direct' = DIRECT_POST (requiere audit)
INSERT INTO policy_params (key, value) VALUES
    ('tiktok_publish_mode', '"inbox"'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- privacy del Direct Post (solo se usa en modo 'direct')
-- Valores aceptados por TikTok: PUBLIC_TO_EVERYONE, MUTUAL_FOLLOW_FRIENDS,
-- FOLLOWER_OF_CREATOR, SELF_ONLY
INSERT INTO policy_params (key, value) VALUES
    ('tiktok_privacy_level', '"SELF_ONLY"'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- toggle global para encender/apagar la subida a TikTok
INSERT INTO policy_params (key, value) VALUES
    ('tiktok_enabled', 'true'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- Mostrar el estado tras aplicar
SELECT key, value, updated_at
FROM policy_params
WHERE key LIKE 'tiktok%' OR key = 'max_tiktok_uploads_per_day'
ORDER BY key;
