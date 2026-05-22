#!/usr/bin/env bash
# Test end-to-end de WF3: produce un short desde un candidato queued.
# Requiere: stack levantado, OPENAI_API_KEY en .env, candidato con status='queued' en DB.
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
  echo "No hay candidatos en estado 'queued'."
  echo "Ejecuta antes WF1_Ingestion + WF2_Curation, o promueve uno manualmente:"
  echo "  docker compose exec postgres psql -U horror -d horror_shorts -c \\"
  echo "    \"UPDATE app.reddit_candidates SET status='queued', quality_score=8, horror_score=8 WHERE id=1;\""
  exit 1
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
