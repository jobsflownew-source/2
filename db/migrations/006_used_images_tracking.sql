-- =====================================================
-- Migration 006: tracking de imagenes usadas en shorts
--
-- Problema: el sistema reutilizaba las mismas imagenes una y otra vez
-- porque siempre tomaba el primer resultado portrait que devolvia
-- Pixabay/Unsplash/Pexels para keywords parecidas.
--
-- Solucion: nueva tabla 'used_images' que registra cada URL servida,
-- la cantidad de veces que se ha usado, y cuando se uso por ultima vez.
-- El pipeline excluye URLs vistas en los ultimos N dias antes de elegir
-- una nueva imagen.
--
-- Aplicar:
--   docker compose exec -T postgres psql -U horror -d horror_shorts \
--     < db/migrations/006_used_images_tracking.sql
-- =====================================================

SET search_path TO app, public;

CREATE TABLE IF NOT EXISTS used_images (
    id              SERIAL PRIMARY KEY,
    image_url       TEXT NOT NULL UNIQUE,
    source          TEXT NOT NULL,          -- 'pixabay' | 'unsplash' | 'pexels' | 'placeholder'
    first_used_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    times_used      INT NOT NULL DEFAULT 1,
    -- Trazabilidad opcional al primer uso
    first_script_id INT,
    first_segment   INT
);

CREATE INDEX IF NOT EXISTS idx_used_images_recent
    ON used_images (last_used_at DESC);
CREATE INDEX IF NOT EXISTS idx_used_images_source
    ON used_images (source);

-- Politica: cooldown en dias antes de permitir reutilizar la misma imagen.
-- 30 dias es razonable: 10 shorts/dia * 30 dias = 300 imagenes en cooldown,
-- muy debajo del catalogo de Pixabay/Unsplash (millones). Ajustable.
INSERT INTO policy_params (key, value) VALUES
    ('image_cooldown_days', '30'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- (Opcional) Backfill de URLs ya usadas en assets historicos, para no
-- repetir las que ya aparecieron en shorts producidos antes de esta migracion.
-- Lee storage_url de assets de tipo 'image' source 'pixabay/unsplash/pexels'.
-- Nota: storage_url apunta a MinIO (re-uploaded), no a la URL original.
-- Si quieres backfill desde el meta JSON original, usa esto:
INSERT INTO used_images (image_url, source, first_used_at, last_used_at, first_script_id, first_segment)
SELECT
    a.meta->>'image_url' AS image_url,
    a.source,
    MIN(a.created_at)   AS first_used_at,
    MAX(a.created_at)   AS last_used_at,
    MIN(a.script_id)    AS first_script_id,
    MIN(a.segment_index) AS first_segment
FROM assets a
WHERE a.type = 'image'
  AND a.meta ? 'image_url'
  AND (a.meta->>'image_url') IS NOT NULL
  AND a.source IN ('pixabay','unsplash','pexels')
GROUP BY a.meta->>'image_url', a.source
ON CONFLICT (image_url) DO NOTHING;

-- Mostrar el estado tras aplicar
SELECT
    'used_images_total'    AS metric, COUNT(*)::text AS value FROM used_images
UNION ALL SELECT
    'cooldown_days_policy', value::text
    FROM policy_params WHERE key = 'image_cooldown_days';
