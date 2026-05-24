-- =====================================================
-- Migration 007: seleccion de imagen con IA (vision)
--
-- En vez de elegir aleatoriamente entre las imagenes disponibles,
-- el pipeline ahora envia 5-8 thumbnails a GPT-4o-mini con vision
-- y le pide que elija la que mejor encaje con el segmento narrativo
-- (texto, mood, keywords).
--
-- Coste extra: ~0.0008 USD por short (5 imagenes a 85 tokens c/u
-- low-detail vision + ~200 tokens de prompt). Para 10 shorts/dia *
-- 30 dias = ~0.24 USD/mes adicionales. Aceptable.
--
-- Configurable via policy_params.image_ai_selection (true/false).
-- Si esta off, el sistema vuelve al comportamiento de PR #6 (random
-- entre las disponibles, sin reutilizar las viejas).
--
-- Aplicar:
--   docker compose exec -T postgres psql -U horror -d horror_shorts \
--     < db/migrations/007_ai_image_selection.sql
-- =====================================================

SET search_path TO app, public;

-- Toggle global para activar/desactivar el scoring con IA
INSERT INTO policy_params (key, value) VALUES
    ('image_ai_selection', 'true'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- Cuantas candidatas mostrar a la IA por segmento. Valor optimo: 5-8.
-- Mas = mejor seleccion pero mas tokens. Menos = mas barato pero peor.
INSERT INTO policy_params (key, value) VALUES
    ('image_ai_candidates_count', '6'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- Modelo de vision a usar. gpt-4o-mini soporta vision desde Jul-2024
-- y es 16x mas barato que gpt-4o (mismo formato multimodal).
INSERT INTO policy_params (key, value) VALUES
    ('image_ai_model', '"gpt-4o-mini"'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- Las columnas de tracking del scoring se guardan en assets.meta como
-- JSON, no necesitan columna nueva. Ejemplo de meta tras esta migracion:
--   {
--     "author": "Pixabay-User",
--     "keywords": ["dark","fog","forest"],
--     "image_url": "https://pixabay.com/...",
--     "ai_selection": {
--       "model": "gpt-4o-mini",
--       "candidates_count": 6,
--       "chosen_index": 2,
--       "score": 9,
--       "reasoning": "best matches the despair mood with low light",
--       "cost_usd": 0.00012
--     }
--   }

-- Mostrar el estado tras aplicar
SELECT key, value, updated_at
FROM policy_params
WHERE key IN ('image_ai_selection', 'image_ai_candidates_count', 'image_ai_model')
ORDER BY key;
