# Setup Guide — Horror Shorts MVP

Guia completa para desplegar el MVP en un VPS Hetzner CX22 (~$8/mes) o
en local para desarrollo.

## 1. Requisitos previos

- VPS o maquina local con Linux (Ubuntu 22.04+ recomendado).
- 4 vCPU / 8 GB RAM minimo (para render de video).
- Docker + docker compose v2.
- 30 GB de disco (assets generados se acumulan).
- Acceso a las APIs externas (ver [Cuentas necesarias](#2-cuentas-y-credenciales-necesarias)).

```bash
# Instalar Docker en Ubuntu
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# logout/login
```

## 2. Cuentas y credenciales necesarias

### Reddit (gratis)
1. Ve a https://www.reddit.com/prefs/apps
2. **Create App** → tipo **script** → redirect URI: `http://localhost:8080`
3. Copia `client_id` (debajo del nombre) y `client_secret`
4. En `.env`:
   - `REDDIT_CLIENT_ID`
   - `REDDIT_CLIENT_SECRET`
   - `REDDIT_USERNAME`, `REDDIT_PASSWORD` (de tu cuenta)

### OpenAI (~$5/mes para 150 shorts)
1. https://platform.openai.com/api-keys → crear key
2. Anade $5 de credito prepagado
3. `OPENAI_API_KEY=sk-...`

### Azure Speech (gratis 500k chars/mes — opcional)
1. Crea cuenta gratis en https://portal.azure.com
2. **Create resource** → **Speech** → tier `F0` (free)
3. Copia `KEY 1` y la `Region`
4. `.env`: `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION`
5. **Si no tienes Azure**, el sistema usa Edge-TTS automaticamente (gratis tambien).

### Stock images (todas gratis)
- **Pixabay**: https://pixabay.com/api/docs/ → `PIXABAY_API_KEY`
- **Unsplash**: https://unsplash.com/developers → `UNSPLASH_ACCESS_KEY`
- **Pexels**: https://www.pexels.com/api/ → `PEXELS_API_KEY`

### YouTube Data API v3
1. Google Cloud Console → crear proyecto
2. Habilita **YouTube Data API v3**
3. **Credentials** → **OAuth 2.0 Client ID** → tipo `Desktop`
4. Descarga JSON, ejecuta script de obtener refresh token (ver `scripts/youtube_oauth.py`, no incluido en MVP)
5. `.env`: `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`

### Discord (webhooks)
1. En tu servidor: **Server Settings** → **Integrations** → **Webhooks** → **New Webhook**
2. Copia URL → `DISCORD_WEBHOOK_URL`

## 3. Arranque rapido

```bash
git clone <repo-url>
cd 2
chmod +x scripts/bootstrap.sh
./scripts/bootstrap.sh
```

El script:
1. Genera `.env` con secretos aleatorios (Postgres, n8n, Render, MinIO).
2. Build del render-service (~5 min primera vez).
3. `docker compose up -d`.
4. Imprime credenciales generadas.

Edita `.env` para anadir las claves de APIs externas:

```bash
nano .env
docker compose restart
```

## 4. Importar workflows en n8n

1. Abre `http://<TU_IP>:5678` (login con `admin` / la pass que imprimio bootstrap).
2. **Settings** → **Credentials** → **Add credential**:
   - **Postgres** llamada `Postgres app`:
     - Host: `postgres`, Port: `5432`
     - Database: el de `POSTGRES_DB` (`horror_shorts`)
     - User/Password: los de `.env`
     - Schema: `app`
   - **HTTP Basic Auth** llamada `Reddit Basic Auth (client_id:client_secret)`:
     - User: `REDDIT_CLIENT_ID`
     - Password: `REDDIT_CLIENT_SECRET`
3. **Workflows** → **Import from file**:
   - `n8n/workflows/WF1_Ingestion.json`
   - `n8n/workflows/WF2_Curation.json`
4. Activa cada workflow (toggle arriba a la derecha).
5. Ejecuta manualmente WF1 una vez (boton **Execute Workflow**) para verificar.

## 5. Verificar funcionamiento

```bash
# Render-service health
curl http://localhost:8000/health

# Probar TTS (sin claves externas)
chmod +x scripts/test_render.sh
./scripts/test_render.sh

# Ver candidates ingestados
docker compose exec postgres psql -U horror -d horror_shorts -c \
  "SELECT id, title, score, num_comments, status FROM app.reddit_candidates ORDER BY id DESC LIMIT 10;"
```

## 6. Cron y mantenimiento

Los workflows traen sus propios crons. Para mantenimiento adicional:

```bash
# Limpiar assets antiguos (opcional, guardalo en cron)
0 3 * * * docker exec horror_postgres psql -U horror -d horror_shorts -c \
  "DELETE FROM app.reddit_candidates WHERE status='rejected' AND fetched_at < now() - INTERVAL '30 days';"
```

## 7. Costes reales esperados (MVP)

Despues del primer mes (datos reales aproximados):

| Servicio | Uso | Coste |
|---|---|---|
| VPS Hetzner CX22 | 4 vCPU, 8 GB | $8 |
| Storage (MinIO local en VPS) | ~30 GB | $0 |
| Reddit API | 200 req/dia | $0 |
| OpenAI gpt-4o-mini | ~10 candidates/dia × 4k tokens | $4-6 |
| Edge-TTS | Ilimitado | $0 |
| Pixabay/Unsplash/Pexels | <1000 req/dia | $0 |
| Discord webhooks | Ilimitados | $0 |
| **TOTAL** | | **~$12-14/mes** |

> Si activas Azure TTS: gratis hasta 500k chars/mes (~250 shorts).
> Si superas el free tier: $4/1M chars con Azure Neural.

## 8. Limites del MVP

Este MVP **NO** incluye todavia (planificado para fases siguientes):

- WF3 produccion completa (guion → audio → imagenes → render)
- WF4 subida a YouTube
- WF5 analytics
- WF6 optimizer (multi-armed bandit)
- WF7 respuesta a comentarios
- WF8 multi-idioma
- Generacion de miniaturas
- Generacion de imagenes IA (usa solo stock)

Lo que SI funciona end-to-end con este MVP:
- Ingesta de candidatos desde Reddit (WF1)
- Scoring y filtrado con GPT (WF2)
- TTS funcional (Edge-TTS gratis)
- Busqueda de imagenes stock
- Render manual de un short via API `/render`

## 9. Troubleshooting

### `docker compose up` falla con permission denied en `init.sql`
```bash
chmod 644 db/init.sql
docker compose down -v && docker compose up -d
```

### n8n no encuentra el schema `app`
- Ejecuta el SQL manualmente:
```bash
docker compose exec -T postgres psql -U horror -d horror_shorts < db/init.sql
```

### Edge-TTS falla con 403
- Es un servicio no oficial de Microsoft. Si bloquean tu IP, configura
  Azure Speech (free tier) o usa una VPN para desarrollo.

### Render falla por OOM
- Sube el VPS a CCX13 (8 GB → 16 GB) durante renders intensos, o
  limita a 1 render concurrente con un Redis lock.

## 10. Roadmap

Ver issues del repo. Proximos hitos:
- Sprint 1: WF3 produccion (este es el grande)
- Sprint 2: WF4 publicacion en YouTube
- Sprint 3: WF5 analytics + WF6 optimizer
