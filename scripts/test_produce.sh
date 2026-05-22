#!/usr/bin/env bash
# Test end-to-end: si no hay candidates, genera uno con IA. Despues produce un short.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

API="${RENDER_URL:-http://localhost:8000}"
KEY="${RENDER_API_KEY:-$(grep '^RENDER_API_KEY=' .env | cut -d= -f2)}"

if [ -z "$KEY" ]; then
  echo "ERROR: RENDER_API_KEY no encontrada en .env"
  exit 1
fi

echo "1) Buscando un candidato queued..."
QUEUE=$(curl -sf "$API/produce/queue?limit=1" -H "X-API-Key: $KEY")
echo "$QUEUE" | jq .

CID=$(echo "$QUEUE" | jq -r '.[0].candidate_id // empty')

if [ -z "$CID" ]; then
  echo ""
  echo "No hay candidatos. Generando una historia con IA..."
  GEN=$(curl -sf -X POST "$API/stories/generate" \
    -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
    -d '{"count": 1, "language": "es", "auto_queue": true}')
  echo "$GEN" | jq '{created, total_cost_usd, candidates: .candidates | map({candidate_id, title, theme, setting})}'
  CID=$(echo "$GEN" | jq -r '.candidates[0].candidate_id // empty')
  if [ -z "$CID" ]; then
    echo "ERROR: la generacion fallo. Revisa OPENAI_API_KEY en .env."
    exit 1
  fi
fi

echo ""
echo "2) Estado de produccion hoy:"
curl -sf "$API/produce/today" -H "X-API-Key: $KEY" | jq .

echo ""
echo "3) Lanzando produccion del candidate_id=$CID (1-3 minutos)..."
RESULT=$(curl -sf -X POST "$API/produce" \
  -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d "{\"candidate_id\": $CID, \"language\": \"es\"}" \
  --max-time 600)

echo "$RESULT" | jq '{
  short_id, script_id, title, duration_sec,
  segments_count, file_size_mb: (.file_size_bytes / 1024 / 1024 | floor),
  video_url
}'

VIDEO_URL=$(echo "$RESULT" | jq -r '.video_url')
echo ""
echo "OK Short producido: $VIDEO_URL"
echo "(abre la URL en navegador para verlo, o descarga con: curl -O '$VIDEO_URL')"
