# Setup Guide — Horror Shorts MVP (100% AI)

Guia completa para desplegar el MVP en un VPS Hetzner CX22 (~$8/mes) o
en local para desarrollo.

## 1. Requisitos previos

- VPS o maquina local con Linux (Ubuntu 22.04+ recomendado).
- 4 vCPU / 8 GB RAM minimo (para render de video).
- Docker + docker compose v2.
- 30 GB de disco (assets generados se acumulan).
- Acceso a las APIs externas (ver siguiente seccion).

```bash
# Instalar Docker en Ubuntu
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# logout/login
```

## 2. Cuentas y credenciales necesarias

### OpenAI (REQUERIDO, ~$5/mes para 150 shorts)
1. https://platform.openai.com/api-keys → crear key
2. **Settings → Billing**: anade $5 de credito prepagado
3. `.env`: `OPENAI_API_KEY=sk-...`

### Stock images (todas gratis, OPCIONALES — el sistema funciona con cualquiera)
- **Pixabay**: https://pixabay.com/api/docs/ → `PIXABAY_API_KEY`
- **Unsplash**: https://unsplash.com/developers → `UNSPLASH_ACCESS_KEY`
- **Pexels**: https://www.pexels.com/api/ → `PEXELS_API_KEY`

> Recomendado: configura **al menos una** (Pixabay es la mas rapida de obtener).

### Azure Speech (gratis 500k chars/mes — OPCIONAL)
1. Cuenta gratis en https://portal.azure.com
2. **Create resource** → **Speech** → tier `F0` (free)
3. Copia `KEY 1` y la `Region`
4. `.env`: `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION`

> **Si no tienes Azure**, el sistema usa **Edge-TTS** automaticamente (gratis tambien).

### Discord (webhooks — OPCIONAL pero recomendado)
1. En tu servidor Discord: **Server Settings** → **Integrations** → **Webhooks** → **New Webhook**
2. Copia URL → `DISCORD_WEBHOOK_URL`

### YouTube Data API v3 (futuro WF4 — no necesario para MVP actual)
Skip por ahora. Lo configuraremos cuando construyamos WF4_Publish.

## 3. Arranque rapido

```bash
git clone https://github.com/<tu-org>/<tu-repo>.git horror
cd horror
git checkout feat/ai-story-generation
chmod +x scripts/bootstrap.sh
./scripts/bootstrap.sh
```

El script:
1. Genera `.env` con secretos aleatorios (Postgres, n8n, Render, MinIO).
2. Build del render-service (~5 min primera vez).
3. `docker compose up -d`.
4. Imprime credenciales generadas.

Edita `.env` para anadir las claves API:

```bash
nano .env
docker compose restart
```

## 4. Importar workflows en n8n

1. Abre `http://<TU_IP>:5678` (login `admin` / pass que imprimio bootstrap).
2. **Settings (rueda) → Credentials → Add credential**:
   - **Postgres** llamada `Postgres app`:
     - Host: `postgres`, Port: `5432`
     - Database: `horror_shorts`
     - User/Password: los de `.env`
     - SSL: `disable`
3. **Workflows** → **Import from file**:
   - `n8n/workflows/WF1_AIStoryGen.json`
   - `n8n/workflows/WF3_Production.json`
4. Para cada workflow importado: click en los nodos Postgres y selecciona la credencial `Postgres app`.
5. Activa cada workflow (toggle "Active" arriba derecha).

## 5. Verificar funcionamiento

### 5.1 Salud del API
```bash
curl http://localhost:8000/health
# {"status":"ok","version":"0.1.0"}
```

### 5.2 Smoke test TTS + stock images (sin coste)
```bash
chmod +x scripts/test_render.sh
./scripts/test_render.sh
```

### 5.3 Generar la primera historia con IA (~$0.02)
Desde n8n: **Execute Workflow** en `WF1_AIStoryGen`. O directamente con curl:
```bash
KEY=$(grep '^RENDER_API_KEY=' .env | cut -d= -f2)
curl -X POST http://localhost:8000/stories/generate \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"count": 1, "language": "es", "auto_queue": true}' | jq
```

Verifica en DB:
```bash
docker compose exec postgres psql -U horror -d horror_shorts -c \
  "SELECT id, source, title, theme, setting, quality_score, status FROM app.story_candidates ORDER BY id DESC LIMIT 5;"
```

### 5.4 Producir el primer short (1-3 min)
```bash
chmod +x scripts/test_produce.sh
./scripts/test_produce.sh
```

Te imprimira la URL del MP4 final. Abrela en el navegador.

## 6. Configuracion fina

Todo se ajusta en la tabla `policy_params`:

```bash
# Cambiar idioma por defecto
docker compose exec postgres psql -U horror -d horror_shorts -c \
  "UPDATE app.policy_params SET value='\"en\"'::jsonb WHERE key='default_language';"

# Aumentar a 8 historias/dia
docker compose exec postgres psql -U horror -d horror_shorts -c \
  "UPDATE app.policy_params SET value='8'::jsonb WHERE key='stories_per_day';"

# Bajar produccion a 3 shorts/dia
docker compose exec postgres psql -U horror -d horror_shorts -c \
  "UPDATE app.policy_params SET value='3'::jsonb WHERE key='max_shorts_per_day';"

# Anadir un theme nuevo
docker compose exec postgres psql -U horror -d horror_shorts -c \
  "UPDATE app.policy_params SET value=value || '[\"objeto familiar que aparece donde no debe\"]'::jsonb WHERE key='story_themes';"
```

## 7. Costes reales esperados (MVP)

| Servicio | Uso | Coste |
|---|---|---|
| VPS Hetzner CX22 | 4 vCPU, 8 GB | $8 |
| Storage MinIO local | ~30 GB | $0 |
| OpenAI gpt-4o-mini | ~150 stories+scripts/mes | $4-6 |
| Edge-TTS / Azure free | Ilimitado / 500k chars | $0 |
| Pixabay / Unsplash / Pexels | <1000 req/dia | $0 |
| Discord webhooks | Ilimitados | $0 |
| **TOTAL** | | **~$12-14/mes** |

## 8. Limites del MVP actual

**Funciona end-to-end:**
- ✅ Generacion de historias originales con IA (WF1)
- ✅ Adaptacion a guion segmentado para Shorts
- ✅ TTS + busqueda de imagenes stock
- ✅ Render MP4 9:16 con Ken Burns + subtitulos quemados
- ✅ Almacenamiento de assets y video final en MinIO
- ✅ Tracking completo de costes en `cost_ledger`

**Pendiente (proximos sprints):**
- WF4 subida automatica a YouTube
- WF5 analytics + recoleccion de metricas
- WF6 optimizer (multi-armed bandit para voces, themes, horarios)
- WF7 respuesta a comentarios con IA
- WF8 multi-idioma (traduccion + redoblaje)
- Generacion de imagenes IA propias (Stable Diffusion)
- Miniaturas custom

## 9. Troubleshooting

### `docker compose up` falla con permission denied en `init.sql`
```bash
chmod 644 db/init.sql
docker compose down -v && docker compose up -d
```

### `WF1_AIStoryGen` falla con "OPENAI_API_KEY no configurada"
- Verifica `.env`: `OPENAI_API_KEY=sk-...`
- `docker compose restart render-service`

### Edge-TTS falla con 403 (raro pero pasa)
- Microsoft puede bloquear IPs de datacenters. Configura Azure (free tier).

### Render falla por OOM
- Sube el VPS a CCX13 (8 → 16 GB), o limita a 1 produccion concurrente.

### Las historias se sienten repetitivas
- Anade mas themes/settings a `policy_params` (ver seccion 6).
- Sube `temperature` editando `story_gen.py` linea ~115 (default 0.85).

## 10. Roadmap

- Sprint 1: WF4 publicacion en YouTube + miniaturas
- Sprint 2: WF5 analytics + WF6 optimizer
- Sprint 3: WF7 comentarios + WF8 multi-idioma
- Sprint 4: imagenes IA propias (Stable Diffusion via RunPod)

## 11. API endpoints del render-service (referencia)

Todos requieren header `X-API-Key: $RENDER_API_KEY`.

| Metodo | Path | Descripcion |
|---|---|---|
| GET | `/health` | Healthcheck |
| GET | `/tts/voices?language=es` | Lista voces Edge-TTS |
| POST | `/tts` | Sintetiza un texto, devuelve URL del MP3 |
| POST | `/images/search` | Busca imagen stock por keywords |
| POST | `/render` | Renderiza un short con segments preparados |
| POST | `/script` | Genera (o reusa) guion JSON desde candidate_id |
| POST | `/produce` | Pipeline completo candidate -> MP4 (timeout 10min) |
| GET | `/produce/queue?limit=10` | Lista candidatos `queued` pendientes |
| GET | `/produce/today?language=es` | Cuantos shorts producidos hoy |
| **POST** | **`/stories/generate`** | **Genera N historias IA originales** |
| GET | `/stories/today?language=es` | Cuantas historias generadas hoy |

Ejemplo de flujo manual completo:
```bash
KEY=$(grep '^RENDER_API_KEY=' .env | cut -d= -f2)

# 1. Genera 1 historia
RESP=$(curl -sX POST http://localhost:8000/stories/generate \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"count": 1, "language": "es"}')
CID=$(echo $RESP | jq -r '.candidates[0].candidate_id')

# 2. Produce el short
curl -X POST http://localhost:8000/produce \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d "{\"candidate_id\": $CID, \"language\": \"es\"}"
```
