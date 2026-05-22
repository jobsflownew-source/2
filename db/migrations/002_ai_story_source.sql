-- =====================================================
-- Migration 002: Renombrar reddit_candidates -> story_candidates
-- y soportar multi-source (AI, Reddit, public domain).
--
-- Aplicar SOLO si ya tienes la v1 desplegada en produccion.
-- Para instalaciones nuevas, init.sql ya esta actualizado.
--
-- Uso:
--   docker compose exec -T postgres psql -U horror -d horror_shorts \
--     < db/migrations/002_ai_story_source.sql
-- =====================================================

SET search_path TO app, public;

-- 1. Renombrar tabla
ALTER TABLE IF EXISTS reddit_candidates RENAME TO story_candidates;

-- 2. Anadir nuevas columnas
ALTER TABLE story_candidates
    ADD COLUMN IF NOT EXISTS source TEXT,
    ADD COLUMN IF NOT EXISTS external_id TEXT,
    ADD COLUMN IF NOT EXISTS language TEXT,
    ADD COLUMN IF NOT EXISTS theme TEXT,
    ADD COLUMN IF NOT EXISTS setting TEXT,
    ADD COLUMN IF NOT EXISTS tone TEXT,
    ADD COLUMN IF NOT EXISTS word_count INT,
    ADD COLUMN IF NOT EXISTS self_assessment TEXT,
    ADD COLUMN IF NOT EXISTS gen_model TEXT,
    ADD COLUMN IF NOT EXISTS gen_cost_usd NUMERIC(10,5);

-- 3. Migrar datos existentes
UPDATE story_candidates
SET source = COALESCE(source, 'reddit'),
    language = COALESCE(language, 'en'),
    external_id = COALESCE(external_id, reddit_id)
WHERE source IS NULL OR external_id IS NULL;

-- 4. Constraints
ALTER TABLE story_candidates
    ALTER COLUMN source SET DEFAULT 'ai_generated',
    ALTER COLUMN source SET NOT NULL,
    ALTER COLUMN language SET DEFAULT 'es',
    ALTER COLUMN language SET NOT NULL;

-- Drop check antiguo si existe y recrear
ALTER TABLE story_candidates
    DROP CONSTRAINT IF EXISTS reddit_candidates_status_check;
ALTER TABLE story_candidates
    DROP CONSTRAINT IF EXISTS story_candidates_status_check;
ALTER TABLE story_candidates
    ADD CONSTRAINT story_candidates_status_check
    CHECK (status IN ('new','scored','queued','produced','rejected','blacklisted'));

ALTER TABLE story_candidates
    DROP CONSTRAINT IF EXISTS story_candidates_source_check;
ALTER TABLE story_candidates
    ADD CONSTRAINT story_candidates_source_check
    CHECK (source IN ('ai_generated','reddit','public_domain','manual'));

-- subreddit y reddit_id pasan a ser opcionales
ALTER TABLE story_candidates
    ALTER COLUMN reddit_id DROP NOT NULL,
    ALTER COLUMN score DROP NOT NULL,
    ALTER COLUMN num_comments DROP NOT NULL;

-- score y num_comments default 0 si vienen de AI (no aplican)
ALTER TABLE story_candidates
    ALTER COLUMN score DROP DEFAULT,
    ALTER COLUMN num_comments DROP DEFAULT;

-- 5. Unique compuesto en lugar del unique de reddit_id
ALTER TABLE story_candidates DROP CONSTRAINT IF EXISTS reddit_candidates_reddit_id_key;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'story_candidates_source_external_id_key'
    ) THEN
        ALTER TABLE story_candidates
            ADD CONSTRAINT story_candidates_source_external_id_key
            UNIQUE (source, external_id);
    END IF;
END $$;

-- 6. Renombrar foreign keys que apuntaban al nombre viejo
DO $$
BEGIN
    -- scripts.candidate_id ya apunta correctamente (la tabla destino es la misma)
    -- nada que hacer, postgres mantiene la FK al renombrar
    NULL;
END $$;

-- 7. Indices
DROP INDEX IF EXISTS idx_candidates_source;
CREATE INDEX IF NOT EXISTS idx_candidates_source
    ON story_candidates (source, status);

-- 8. Eliminar reddit_blacklist (ya no necesaria)
DROP TABLE IF EXISTS reddit_blacklist;

-- 9. Refrescar policy_params con nuevas claves para historias IA
INSERT INTO policy_params (key, value) VALUES
    ('stories_per_day', '5'::jsonb),
    ('default_language', '"es"'::jsonb),
    ('story_themes', '[
        "ritual antiguo redescubierto por accidente",
        "entidad atrapada en un objeto cotidiano",
        "doble identico que reemplaza al original",
        "transmision que no deberia existir",
        "bucle temporal con reglas crueles",
        "trabajo nocturno con normas que no debes romper",
        "foto o grabacion que cambia con el tiempo",
        "vecino o conocido que no es lo que parece",
        "infancia que regresa con un significado oscuro",
        "promesa hecha hace anos que se cobra ahora",
        "juego inocente con consecuencias catastroficas",
        "presencia que solo aparece a una hora especifica",
        "regla familiar nunca explicada que ahora entiende",
        "testigo de algo que el resto no recuerda",
        "casa que cambia su geometria por la noche"
    ]'::jsonb),
    ('story_settings', '[
        "metro de madrugada vacio",
        "cabana en el bosque sin senal",
        "hospital de noche pasillo cerrado",
        "carretera secundaria entre niebla",
        "edificio de oficinas piso 13 fuera de horario",
        "habitacion de hotel de carretera",
        "sotano de casa familiar",
        "garaje subterraneo a las 4am",
        "supermercado 24h sin clientes",
        "playa abandonada en invierno",
        "iglesia rural cerrada por restauracion",
        "camping fuera de temporada",
        "biblioteca antigua seccion restringida",
        "estacion de tren rural sin personal",
        "pueblo de montana desconectado por nieve"
    ]'::jsonb),
    ('story_tones', '[
        "psicologico sutil",
        "sobrenatural mundano",
        "creeping dread",
        "folk horror",
        "liminal"
    ]'::jsonb)
ON CONFLICT (key) DO NOTHING;
