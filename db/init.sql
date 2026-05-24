-- =====================================================
-- Horror Shorts Automation - PostgreSQL Schema
-- =====================================================

-- Schema dedicado para n8n (lo crea solo, pero lo declaramos)
CREATE SCHEMA IF NOT EXISTS n8n;

-- Schema de la aplicacion
CREATE SCHEMA IF NOT EXISTS app;
SET search_path TO app, public;

-- Extensiones utiles
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- =====================================================
-- 1. Candidatos de historias (multi-source)
-- =====================================================
-- source = 'ai_generated' (default) | 'reddit' | 'public_domain' | 'manual'
CREATE TABLE IF NOT EXISTS story_candidates (
    id              SERIAL PRIMARY KEY,
    source          TEXT NOT NULL DEFAULT 'ai_generated'
        CHECK (source IN ('ai_generated','reddit','public_domain','manual')),
    external_id     TEXT,                  -- id en la fuente externa (reddit_id si aplica)
    author          TEXT,                  -- 'AI' o el autor real si reddit/public
    title           TEXT NOT NULL,
    selftext        TEXT NOT NULL,
    selftext_hash   CHAR(32) NOT NULL,
    url             TEXT,
    permalink       TEXT,
    -- Metadatos generales
    language        TEXT NOT NULL DEFAULT 'es',
    theme           TEXT,                  -- el "tema" usado para generar (IA)
    setting         TEXT,                  -- el "setting" usado para generar (IA)
    tone            TEXT,                  -- el tono usado (IA)
    word_count      INT,
    -- Reddit-specific (NULL para IA)
    score           INT,
    num_comments    INT,
    upvote_ratio    NUMERIC(4,3),
    over_18         BOOLEAN NOT NULL DEFAULT FALSE,
    created_utc     TIMESTAMPTZ,
    -- Scoring
    quality_score   NUMERIC(5,2),
    horror_score    NUMERIC(5,2),
    self_assessment TEXT,
    rejection_reason TEXT,
    -- Coste de generacion (solo IA)
    gen_model       TEXT,
    gen_cost_usd    NUMERIC(10,5),
    -- Lifecycle
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    status          TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('new','scored','queued','produced','rejected','blacklisted')),
    UNIQUE (source, external_id)
);

CREATE INDEX IF NOT EXISTS idx_candidates_status_score
    ON story_candidates (status, quality_score DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_candidates_fetched
    ON story_candidates (fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_candidates_hash
    ON story_candidates (selftext_hash);
CREATE INDEX IF NOT EXISTS idx_candidates_source
    ON story_candidates (source, status);

-- =====================================================
-- 2. Guiones generados por GPT
-- =====================================================
CREATE TABLE IF NOT EXISTS scripts (
    id                  SERIAL PRIMARY KEY,
    candidate_id        INT NOT NULL REFERENCES story_candidates(id) ON DELETE CASCADE,
    language            TEXT NOT NULL DEFAULT 'es',
    title               TEXT NOT NULL,
    seo_description     TEXT,
    tags                TEXT[] NOT NULL DEFAULT '{}',
    hook                TEXT,
    segments            JSONB NOT NULL,
    -- segments es array de objetos:
    -- { id, text, estimated_duration_sec, keywords[], image_prompt, mood, sub_shots[] }
    total_estimated_sec NUMERIC(6,2),
    llm_model           TEXT,
    llm_input_tokens    INT,
    llm_output_tokens   INT,
    llm_cost_usd        NUMERIC(10,5),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (candidate_id, language)
);

CREATE INDEX IF NOT EXISTS idx_scripts_candidate ON scripts (candidate_id);

-- =====================================================
-- 3. Assets generados (imagenes, audios, videos)
-- =====================================================
CREATE TABLE IF NOT EXISTS assets (
    id              SERIAL PRIMARY KEY,
    script_id       INT NOT NULL REFERENCES scripts(id) ON DELETE CASCADE,
    segment_index   INT,
    sub_shot_index  INT,
    type            TEXT NOT NULL CHECK (type IN ('image','audio','video','thumbnail','subtitles')),
    source          TEXT NOT NULL,  -- 'sd','dalle','unsplash','pixabay','pexels','azure_tts','edge_tts','elevenlabs','ffmpeg'
    storage_url     TEXT NOT NULL,
    duration_ms     INT,
    width           INT,
    height          INT,
    file_size_bytes BIGINT,
    cost_usd        NUMERIC(10,5) DEFAULT 0,
    meta            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_assets_script ON assets (script_id, type);
CREATE INDEX IF NOT EXISTS idx_assets_segment ON assets (script_id, segment_index, sub_shot_index);

-- =====================================================
-- 4. Shorts publicados
-- =====================================================
CREATE TABLE IF NOT EXISTS shorts (
    id                  SERIAL PRIMARY KEY,
    script_id           INT NOT NULL REFERENCES scripts(id) ON DELETE CASCADE,
    parent_short_id     INT REFERENCES shorts(id) ON DELETE SET NULL,
    youtube_video_id    TEXT UNIQUE,
    channel_id          TEXT,
    language            TEXT NOT NULL DEFAULT 'es',
    voice_id            TEXT,
    image_style         TEXT,
    title               TEXT NOT NULL,
    description         TEXT,
    tags                TEXT[] NOT NULL DEFAULT '{}',
    final_video_url     TEXT,
    thumbnail_url       TEXT,
    duration_sec        NUMERIC(5,2),
    file_size_bytes     BIGINT,
    scheduled_for       TIMESTAMPTZ,
    published_at        TIMESTAMPTZ,
    status              TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','rendering','rendered','uploading','published','failed','removed')),
    error_message       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_shorts_status ON shorts (status, scheduled_for);
CREATE INDEX IF NOT EXISTS idx_shorts_published ON shorts (published_at DESC);
CREATE INDEX IF NOT EXISTS idx_shorts_yt_id ON shorts (youtube_video_id);

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_shorts_updated_at ON shorts;
CREATE TRIGGER trg_shorts_updated_at
    BEFORE UPDATE ON shorts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- =====================================================
-- 5. Metricas horarias por short
-- =====================================================
CREATE TABLE IF NOT EXISTS metrics_hourly (
    short_id                INT NOT NULL REFERENCES shorts(id) ON DELETE CASCADE,
    captured_at             TIMESTAMPTZ NOT NULL,
    views                   BIGINT NOT NULL DEFAULT 0,
    likes                   INT NOT NULL DEFAULT 0,
    dislikes                INT NOT NULL DEFAULT 0,
    comments                INT NOT NULL DEFAULT 0,
    shares                  INT NOT NULL DEFAULT 0,
    avg_view_duration_sec   NUMERIC(6,2),
    avg_view_percentage     NUMERIC(5,2),
    impressions             BIGINT,
    ctr                     NUMERIC(6,5),
    subscribers_gained      INT DEFAULT 0,
    estimated_revenue_usd   NUMERIC(10,5) DEFAULT 0,
    PRIMARY KEY (short_id, captured_at)
);

CREATE INDEX IF NOT EXISTS idx_metrics_captured ON metrics_hourly (captured_at DESC);

-- Vista materializada para reportes rapidos (ultimas 24h)
CREATE MATERIALIZED VIEW IF NOT EXISTS shorts_performance_24h AS
SELECT
    s.id AS short_id,
    s.youtube_video_id,
    s.title,
    s.language,
    s.published_at,
    EXTRACT(EPOCH FROM (now() - s.published_at)) / 3600.0 AS hours_since_publish,
    COALESCE(MAX(m.views), 0) AS views,
    COALESCE(MAX(m.likes), 0) AS likes,
    COALESCE(MAX(m.comments), 0) AS comments,
    COALESCE(MAX(m.avg_view_percentage), 0) AS avg_view_pct,
    COALESCE(MAX(m.ctr), 0) AS ctr,
    CASE
        WHEN EXTRACT(EPOCH FROM (now() - s.published_at)) > 0
        THEN COALESCE(MAX(m.views), 0)::numeric
             / (EXTRACT(EPOCH FROM (now() - s.published_at)) / 3600.0)
        ELSE 0
    END AS views_per_hour
FROM shorts s
LEFT JOIN metrics_hourly m
    ON m.short_id = s.id
   AND m.captured_at >= now() - INTERVAL '24 hours'
WHERE s.published_at >= now() - INTERVAL '7 days'
  AND s.status = 'published'
GROUP BY s.id;

CREATE UNIQUE INDEX IF NOT EXISTS idx_perf_24h_short
    ON shorts_performance_24h (short_id);

-- =====================================================
-- 6. Parametros de politica (multi-armed bandit)
-- =====================================================
CREATE TABLE IF NOT EXISTS policy_params (
    key         TEXT PRIMARY KEY,
    value       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seeds iniciales
INSERT INTO policy_params (key, value) VALUES
    ('voices', '[
        "es-ES-AlvaroNeural",
        "es-ES-TristanMultilingualNeural",
        "es-ES-IsidoraMultilingualNeural",
        "es-ES-XimenaNeural",
        "es-MX-JorgeNeural",
        "es-CO-GonzaloNeural"
    ]'::jsonb),
    ('image_styles', '["found_footage","cinematic_horror","polaroid_80s","gothic_painting"]'::jsonb),
    ('publish_hours_utc', '[14,18,21]'::jsonb),
    ('max_shorts_per_day', '5'::jsonb),
    ('max_uploads_per_day', '5'::jsonb),
    ('stories_per_day', '5'::jsonb),
    ('default_language', '"es"'::jsonb),
    ('hook_templates', '["Esto le paso a...","Nunca crei en... hasta que","Si ves esto, no..."]'::jsonb),
    ('min_quality_score', '6.0'::jsonb),
    ('min_horror_score', '6.5'::jsonb),
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

-- =====================================================
-- 7. Stats de los "brazos" del bandit
-- =====================================================
CREATE TABLE IF NOT EXISTS bandit_arms (
    arm_type    TEXT NOT NULL,           -- 'voice', 'image_style', 'hook_template', 'publish_hour'
    arm_value   TEXT NOT NULL,
    pulls       INT NOT NULL DEFAULT 0,
    rewards_sum NUMERIC(12,4) NOT NULL DEFAULT 0,
    last_used   TIMESTAMPTZ,
    PRIMARY KEY (arm_type, arm_value)
);

-- =====================================================
-- 8. Respuestas a comentarios (anti-duplicado)
-- =====================================================
CREATE TABLE IF NOT EXISTS comment_replies (
    comment_id      TEXT PRIMARY KEY,
    short_id        INT REFERENCES shorts(id) ON DELETE CASCADE,
    author          TEXT,
    original_text   TEXT,
    intent          TEXT,
    reply_text      TEXT,
    replied_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =====================================================
-- 9. Dead Letter Queue (jobs que fallaron multiples veces)
-- =====================================================
CREATE TABLE IF NOT EXISTS dlq (
    id              SERIAL PRIMARY KEY,
    workflow        TEXT NOT NULL,
    step            TEXT NOT NULL,
    payload         JSONB NOT NULL,
    error           TEXT,
    attempt_count   INT NOT NULL DEFAULT 1,
    first_failed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_failed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved        BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_dlq_unresolved ON dlq (resolved, last_failed_at DESC);

-- =====================================================
-- 10. Coste acumulado (para dashboards)
-- =====================================================
CREATE TABLE IF NOT EXISTS cost_ledger (
    id          SERIAL PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    service     TEXT NOT NULL,           -- 'openai', 'azure_tts', 'elevenlabs', 'replicate', etc
    operation   TEXT,
    units       NUMERIC(12,4),           -- chars, tokens, segundos, imagenes
    cost_usd    NUMERIC(10,5) NOT NULL,
    script_id   INT REFERENCES scripts(id) ON DELETE SET NULL,
    short_id    INT REFERENCES shorts(id) ON DELETE SET NULL,
    meta        JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_cost_occurred ON cost_ledger (occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_cost_service ON cost_ledger (service, occurred_at DESC);

-- =====================================================
-- Vistas utiles
-- =====================================================
CREATE OR REPLACE VIEW v_daily_costs AS
SELECT
    DATE(occurred_at) AS day,
    service,
    SUM(cost_usd) AS total_usd,
    SUM(units) AS total_units,
    COUNT(*) AS operations
FROM cost_ledger
GROUP BY DATE(occurred_at), service
ORDER BY day DESC, total_usd DESC;

CREATE OR REPLACE VIEW v_shorts_today AS
SELECT
    id, youtube_video_id, language, title,
    published_at, status, duration_sec
FROM shorts
WHERE published_at >= DATE_TRUNC('day', now() AT TIME ZONE 'UTC')
   OR (status IN ('pending','rendering','uploading')
       AND scheduled_for >= DATE_TRUNC('day', now() AT TIME ZONE 'UTC'));

-- =====================================================
-- Permisos basicos (n8n usa el mismo usuario)
-- =====================================================
GRANT USAGE ON SCHEMA app TO PUBLIC;
GRANT ALL ON ALL TABLES IN SCHEMA app TO PUBLIC;
GRANT ALL ON ALL SEQUENCES IN SCHEMA app TO PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA app
    GRANT ALL ON TABLES TO PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA app
    GRANT ALL ON SEQUENCES TO PUBLIC;
