-- =====================================================
-- Migration 009: mejoras de calidad cinematografica
--
-- 1) Banco de tracks de musica ambient horror (royalty-free).
-- 2) Paleta de colores ASS por mood para subtitulos.
-- 3) Toggle de transiciones xfade entre segmentos.
-- 4) Toggle global music_enabled.
--
-- Aplicar:
--   docker compose exec -T postgres psql -U horror -d horror_shorts \
--     < db/migrations/009_quality_upgrades.sql
-- =====================================================

SET search_path TO app, public;

-- 1. Banco de URLs de musica ambient horror (royalty-free).
-- IMPORTANTE: estas son URLs DE EJEMPLO de Pixabay Music (gratis con
-- atribucion en la descripcion del video). Si quieres reemplazarlas
-- por las tuyas, edita esta entrada con tu propio set.
-- Recomendaciones:
--   - https://pixabay.com/music/search/horror-ambient/   (gratis)
--   - https://www.fesliyanstudios.com/                   (gratis con atrib)
--   - https://freepd.com/                                (dominio publico)
INSERT INTO policy_params (key, value) VALUES
    ('music_tracks_horror', '[
        "https://cdn.pixabay.com/audio/2022/10/30/audio_5b03b1c0f8.mp3",
        "https://cdn.pixabay.com/audio/2023/02/28/audio_550d815fc1.mp3",
        "https://cdn.pixabay.com/audio/2024/05/15/audio_4ddbb59b6d.mp3"
    ]'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- Master toggle de musica de fondo
INSERT INTO policy_params (key, value) VALUES
    ('music_enabled', 'true'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- Volumen de la musica de fondo (0.0 - 1.0). 0.15 es discreto.
INSERT INTO policy_params (key, value) VALUES
    ('music_volume', '0.15'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- 2. Colores ASS por mood. Formato ASS: &HBBGGRR& (no RGB).
--    tension   -> rojo crimson
--    fear      -> rojo intenso
--    despair   -> azul oscuro / cian frio
--    reveal    -> blanco brillante (default)
--    aftermath -> gris azulado
INSERT INTO policy_params (key, value) VALUES
    ('subtitle_colors_by_mood', '{
        "tension":   "&H003C3CFF",
        "fear":      "&H001515FF",
        "despair":   "&HFFC080",
        "reveal":    "&HFFFFFF",
        "aftermath": "&HD0D0A0",
        "default":   "&HFFFFFF"
    }'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- Fuente de los subtitulos (debe estar instalada en el contenedor).
-- 'Liberation Sans Bold' es la opcion por defecto: libre, sin EULA, similar
-- a Impact. Esta instalada via fonts-liberation en el Dockerfile.
-- Otras validas sin nuevas fonts: 'DejaVu Sans Bold', 'Roboto'.
INSERT INTO policy_params (key, value) VALUES
    ('subtitle_font', '"Liberation Sans Bold"'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- 3. Toggle de transiciones xfade entre clips
INSERT INTO policy_params (key, value) VALUES
    ('video_transitions_enabled', 'true'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- Duracion en segundos del crossfade entre clips (0.3 - 1.0)
INSERT INTO policy_params (key, value) VALUES
    ('video_transition_sec', '0.4'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- 4. Toggle de hook viral en script_gen
INSERT INTO policy_params (key, value) VALUES
    ('viral_hook_enabled', 'true'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- Mostrar estado tras aplicar
SELECT key, value, updated_at
FROM policy_params
WHERE key IN (
    'music_tracks_horror', 'music_enabled', 'music_volume',
    'subtitle_colors_by_mood', 'subtitle_font',
    'video_transitions_enabled', 'video_transition_sec',
    'viral_hook_enabled'
)
ORDER BY key;
