#!/usr/bin/env bash
# Produce TODOS los candidatos en estado 'queued' uno por uno.
#
# Uso:
#   ./scripts/produce_all.sh           # produce todos los queued
#   ./scripts/produce_all.sh es        # solo los queued en espanol
#   ./scripts/produce_all.sh es 3      # solo los primeros 3
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

LANG="${1:-}"
LIMIT="${2:-100}"
API="${RENDER_URL:-http://localhost:8000}"
KEY="${RENDER_API_KEY:-$(grep '^RENDER_API_KEY=' .env | cut -d= -f2)}"

if [ -z "$KEY" ]; then
  echo "ERROR: RENDER_API_KEY no encontrada en .env"
  exit 1
fi

if ! curl -sf "$API/health" >/dev/null; then
  echo "ERROR: render-service no responde en $API"
  echo "  Ejecuta: docker compose up -d"
  exit 1
fi

if [ -n "$LANG" ]; then
  WHERE="WHERE status='queued' AND language='$LANG'"
else
  WHERE="WHERE status='queued'"
fi

QUEUED=$(docker compose exec -T postgres psql -U horror -d horror_shorts \
  -t -A -c \
  "SELECT id FROM app.story_candidates $WHERE ORDER BY quality_score DESC NULLS LAST, id LIMIT $LIMIT;" \
  | tr -d ' ')

if [ -z "$QUEUED" ]; then
  echo "No hay candidatos en cola."
  echo ""
  echo "Genera nuevos con:  ./scripts/test_aistory.sh"
  exit 0
fi

TOTAL=$(echo "$QUEUED" | wc -l)
echo ">>> Vas a producir $TOTAL Short(s)."
echo ">>> Cada uno tarda 1-3 min (~\$0.005 c/u)."
echo ">>> Tiempo total estimado: ~$((TOTAL * 2)) min."
echo ""
read -r -p "Continuar? (s/N) " RESP
if [[ ! "$RESP" =~ ^[sSyY]$ ]]; then
  echo "Cancelado."
  exit 0
fi

START_TIME=$(date +%s)
SUCCESS=0
FAILED=0
FAILED_IDS=""

i=0
for CID in $QUEUED; do
  i=$((i + 1))
  echo ""
  echo "================================================"
  echo "[$i/$TOTAL] Produciendo candidate_id=$CID"
  echo "================================================"

  RESULT=$(curl -sX POST "$API/produce" \
    -H "X-API-Key: $KEY" \
    -H "Content-Type: application/json" \
    -d "{\"candidate_id\": $CID, \"language\": \"${LANG:-es}\"}" \
    --max-time 600 || echo '{"detail":"curl_failed"}')

  if echo "$RESULT" | jq -e '.video_url' >/dev/null 2>&1; then
    SUCCESS=$((SUCCESS + 1))
    echo "$RESULT" | jq '{
      short_id, title, duration_sec,
      size_mb: ((.file_size_bytes // 0) / 1024 / 1024 | floor),
      video_url
    }'
  else
    FAILED=$((FAILED + 1))
    FAILED_IDS="$FAILED_IDS $CID"
    echo "FALLO en candidate_id=$CID"
    echo "$RESULT" | jq '{detail}' 2>/dev/null || echo "$RESULT"
  fi

  if [ "$i" -lt "$TOTAL" ]; then
    sleep 3
  fi
done

ELAPSED=$(( $(date +%s) - START_TIME ))
MIN=$((ELAPSED / 60))
SEC=$((ELAPSED % 60))

echo ""
echo "================================================"
echo "RESUMEN"
echo "================================================"
echo "  Total intentados:  $TOTAL"
echo "  OK:                $SUCCESS"
echo "  Fallidos:          $FAILED"
[ -n "$FAILED_IDS" ] && echo "  IDs fallidos:     $FAILED_IDS"
echo "  Tiempo:            ${MIN}m ${SEC}s"
echo ""
echo "Coste OpenAI ultima hora:"
docker compose exec -T postgres psql -U horror -d horror_shorts -c \
  "SELECT service, COUNT(*) AS n, ROUND(SUM(cost_usd)::numeric, 4) AS total_usd
   FROM app.cost_ledger
   WHERE occurred_at >= now() - INTERVAL '1 hour'
   GROUP BY service;"
