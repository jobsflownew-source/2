#!/usr/bin/env bash
# Bootstrap script para Horror Shorts Automation MVP
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

green()  { printf "\033[0;32m%s\033[0m\n" "$1"; }
yellow() { printf "\033[0;33m%s\033[0m\n" "$1"; }
red()    { printf "\033[0;31m%s\033[0m\n" "$1"; }

# 1. Verificar dependencias
green "[1/5] Verificando dependencias..."
for cmd in docker openssl; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    red "ERROR: $cmd no esta instalado"
    exit 1
  fi
done

if ! docker compose version >/dev/null 2>&1; then
  red "ERROR: docker compose plugin no instalado"
  exit 1
fi

# 2. Crear .env si no existe
green "[2/5] Configurando .env..."
if [ ! -f .env ]; then
  cp .env.example .env
  # Generar secretos aleatorios
  PG_PASS=$(openssl rand -hex 16)
  N8N_PASS=$(openssl rand -hex 12)
  N8N_KEY=$(openssl rand -hex 32)
  RENDER_KEY=$(openssl rand -hex 24)
  MINIO_PASS=$(openssl rand -hex 16)

  # Reemplazos compatibles con macOS y Linux
  sed -i.bak "s|changeme_strong_pass|$PG_PASS|" .env
  sed -i.bak "s|changeme_n8n|$N8N_PASS|" .env
  sed -i.bak "s|generate_a_random_32_char_string_here|$N8N_KEY|" .env
  sed -i.bak "s|changeme_internal_api_key|$RENDER_KEY|" .env
  sed -i.bak "s|changeme_minio_pass|$MINIO_PASS|" .env
  rm -f .env.bak

  yellow ".env generado con secretos aleatorios."
  yellow "  -> n8n password: $N8N_PASS"
  yellow "  -> Render API key: $RENDER_KEY"
  yellow "  -> MinIO password: $MINIO_PASS"
  yellow "Edita .env para anadir credenciales API (Reddit, OpenAI, Discord, etc)"
else
  green ".env ya existe, no se sobreescribe"
fi

# 3. Build
green "[3/5] Building render-service..."
docker compose build render-service

# 4. Up
green "[4/5] Levantando stack..."
docker compose up -d

# 5. Esperar healthchecks
green "[5/5] Esperando que los servicios esten listos..."
sleep 8

echo ""
green "=== Stack arrancado ==="
echo ""
echo "  n8n:         http://localhost:5678"
echo "  Render API:  http://localhost:8000/docs"
echo "  MinIO UI:    http://localhost:9001"
echo "  Postgres:    localhost:5432"
echo ""
yellow "Siguientes pasos:"
echo "  1. Edita .env con tus credenciales (Reddit, OpenAI, Discord)"
echo "  2. docker compose restart  # para aplicar"
echo "  3. Importa workflows desde n8n/workflows/ en la UI de n8n"
echo "  4. Lee docs/SETUP.md para guia completa"
