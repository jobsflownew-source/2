-- =====================================================
-- Migration 011: Quality Gate con AI antes de publicar
--
-- Tras renderizar el MP4, GPT-4o-mini lee title + hook +
-- segments + voz + duracion y emite un veredicto:
--   {verdict: "publish" | "reject", score: 0-10, reasoning: "..."}
--
-- Si verdict='reject' y score < threshold, el short queda
-- con status='low_quality' y NO se sube a YouTube/TikTok.
-- WF4/WF5 solo recogen status='rendered', asi que low_quality
-- queda fuera del flujo de publicacion automaticamente.
--
-- Coste: ~0.0005 USD por revision (GPT-4o-mini, ~600 tokens).
-- 10 shorts/dia * 30 = ~0.15 USD/mes adicionales. Despreciable.
--
-- Aplicar:
--   docker compose exec -T postgres psql -U horror -d horror_shorts \
--     < db/migrations/011_ai_quality_gate.sql
-- =====================================================

SET search_path TO app, public;

-- 1. Anadir 'low_quality' al CHECK constraint del status de shorts
ALTER TABLE shorts DROP CONSTRAINT IF EXISTS shorts_status_check;
ALTER TABLE shorts ADD CONSTRAINT shorts_status_check
    CHECK (status IN ('pending','rendering','rendered','uploading',
                      'published','failed','removed','low_quality'));

-- 2. Columnas de tracking del veredicto
ALTER TABLE shorts
    ADD COLUMN IF NOT EXISTS quality_verdict   TEXT,
    ADD COLUMN IF NOT EXISTS quality_score_ai  NUMERIC(4,2),
    ADD COLUMN IF NOT EXISTS quality_reasoning TEXT;

-- 3. Toggles en policy_params
INSERT INTO policy_params (key, value) VALUES
    ('quality_gate_enabled', 'true'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- Threshold: shorts con score >= este se publican.
-- 6.0 es razonable; ajusta segun tu tolerancia a calidad media.
INSERT INTO policy_params (key, value) VALUES
    ('quality_gate_min_score', '6.0'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- Modelo de evaluacion (gpt-4o-mini es ~16x mas barato que gpt-4o
-- y suficiente para juzgar coherencia/calidad narrativa).
INSERT INTO policy_params (key, value) VALUES
    ('quality_gate_model', '"gpt-4o-mini"'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- Si false, deja el short en 'rendered' aunque la IA lo rechace
-- (modo "soft": logueas pero no bloqueas).
INSERT INTO policy_params (key, value) VALUES
    ('quality_gate_strict', 'true'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- Mostrar estado tras aplicar
SELECT key, value, updated_at
FROM policy_params
WHERE key LIKE 'quality_gate%'
ORDER BY key;
