-- =====================================================
-- Migration 010: Auto-thumbnails con DALL-E 3
--
-- Tras renderizar el MP4, el pipeline genera un thumbnail
-- cinematografico con DALL-E 3 (1024x1792 vertical), lo
-- procesa para 1080x1920 y lo sube a MinIO. Despues,
-- youtube.upload_video tambien hace videos.thumbnails.set()
-- para que aparezca en el feed/sugerencias.
--
-- Coste: ~$0.040 por thumbnail (DALL-E 3 standard 1024x1792).
-- 10 shorts/dia * 30 dias = ~$12/mes adicionales.
--
-- Aplicar:
--   docker compose exec -T postgres psql -U horror -d horror_shorts \
--     < db/migrations/010_dalle_thumbnails.sql
-- =====================================================

SET search_path TO app, public;

-- Master toggle. Si false, se sigue usando el primer frame del video
-- como thumbnail (comportamiento de YouTube por defecto).
INSERT INTO policy_params (key, value) VALUES
    ('thumbnail_enabled', 'true'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- Modelo de generacion: 'dall-e-3' (calidad alta) | 'dall-e-2' (mas barato pero peor).
INSERT INTO policy_params (key, value) VALUES
    ('thumbnail_model', '"dall-e-3"'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- Calidad: 'standard' ($0.040 por 1024x1792) | 'hd' ($0.080).
-- standard es perfectamente suficiente para Shorts.
INSERT INTO policy_params (key, value) VALUES
    ('thumbnail_quality', '"standard"'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- Tamano: '1024x1792' (vertical 9:16, recomendado para Shorts) |
-- '1024x1024' (cuadrado).
INSERT INTO policy_params (key, value) VALUES
    ('thumbnail_size', '"1024x1792"'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- Style prompt suffix. Se anade al final del prompt construido a partir
-- del titulo + hook + mood del primer segmento. Cambiarlo aqui ajusta el
-- estilo visual de TODOS los thumbnails sin redeploy.
INSERT INTO policy_params (key, value) VALUES
    ('thumbnail_style_suffix', '"cinematic horror, vertical 9:16, dramatic lighting, deep shadows, fog, eerie atmosphere, high contrast, no text, no watermarks, professional movie poster aesthetic"'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- Mostrar estado tras aplicar
SELECT key, value, updated_at
FROM policy_params
WHERE key IN (
    'thumbnail_enabled', 'thumbnail_model',
    'thumbnail_quality', 'thumbnail_size',
    'thumbnail_style_suffix'
)
ORDER BY key;
