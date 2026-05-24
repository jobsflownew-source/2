-- =====================================================
-- Migration 003: Banco curado de voces para terror/misterio
--
-- Sustituye el seed de "voices" por una lista de voces Azure Neural
-- en espanol seleccionadas por su tono atmosferico/narrativo.
-- El sistema rota automaticamente entre ellas via bandit_arms.
--
-- Aplicar:
--   docker compose exec -T postgres psql -U horror -d horror_shorts \
--     < db/migrations/003_curated_voices.sql
-- =====================================================

SET search_path TO app, public;

UPDATE policy_params
SET value = '[
    "es-ES-AlvaroNeural",
    "es-ES-TristanMultilingualNeural",
    "es-ES-IsidoraMultilingualNeural",
    "es-ES-XimenaNeural",
    "es-MX-JorgeNeural",
    "es-CO-GonzaloNeural"
]'::jsonb,
    updated_at = now()
WHERE key = 'voices';

-- Si la fila no existia, insertarla
INSERT INTO policy_params (key, value)
SELECT 'voices', '[
    "es-ES-AlvaroNeural",
    "es-ES-TristanMultilingualNeural",
    "es-ES-IsidoraMultilingualNeural",
    "es-ES-XimenaNeural",
    "es-MX-JorgeNeural",
    "es-CO-GonzaloNeural"
]'::jsonb
WHERE NOT EXISTS (SELECT 1 FROM policy_params WHERE key = 'voices');

-- Mostrar resultado
SELECT key, value, updated_at FROM policy_params WHERE key = 'voices';
