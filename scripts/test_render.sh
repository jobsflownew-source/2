#!/usr/bin/env bash
# Smoke test del render-service
set -euo pipefail

API="${RENDER_URL:-http://localhost:8000}"
KEY="${RENDER_API_KEY:-$(grep '^RENDER_API_KEY=' .env | cut -d= -f2)}"

if [ -z "$KEY" ]; then
  echo "ERROR: RENDER_API_KEY no encontrada en .env"
  exit 1
fi

echo "1) Health check"
curl -sf "$API/health" | jq .

echo ""
echo "2) TTS test (Edge-TTS gratis)"
curl -sf -X POST "$API/tts" \
  -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Aquella noche escuche pasos en el sotano. No habia nadie en casa.",
    "voice": "es-ES-AlvaroNeural",
    "provider": "edge",
    "upload": true
  }' | jq '{provider_used, duration_sec, storage_url, words: (.word_boundaries | length)}'

echo ""
echo "3) Image search"
curl -sf -X POST "$API/images/search" \
  -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "keywords": ["abandoned hospital", "dark corridor"],
    "download": true,
    "upload": true
  }' | jq '{source: .meta.source, storage_url}'

echo ""
echo "OK - tests basicos pasados"
