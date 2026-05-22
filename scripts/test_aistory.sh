#!/usr/bin/env bash
# Smoke test del generador de historias IA.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

API="${RENDER_URL:-http://localhost:8000}"
KEY="${RENDER_API_KEY:-$(grep '^RENDER_API_KEY=' .env | cut -d= -f2)}"
COUNT="${1:-1}"
LANG="${2:-es}"

if [ -z "$KEY" ]; then
  echo "ERROR: RENDER_API_KEY no encontrada en .env"
  exit 1
fi

echo "Generando $COUNT historia(s) en $LANG..."
RESP=$(curl -sf -X POST "$API/stories/generate" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d "{\"count\": $COUNT, \"language\": \"$LANG\", \"auto_queue\": true}" \
  --max-time 300)

echo "$RESP" | jq '{
  requested, created,
  total_cost_usd,
  candidates: .candidates | map({
    id: .candidate_id, title,
    theme, setting, tone,
    words: .word_count,
    quality: .self_quality, horror: .self_horror
  }),
  errors
}'

echo ""
echo "Para ver el texto completo de la primera:"
ID=$(echo "$RESP" | jq -r '.candidates[0].candidate_id // empty')
if [ -n "$ID" ]; then
  echo "  docker compose exec postgres psql -U horror -d horror_shorts -c \\"
  echo "    \"SELECT title, theme, setting, word_count, quality_score, substring(selftext, 1, 800) AS preview FROM app.story_candidates WHERE id=$ID;\""
fi
