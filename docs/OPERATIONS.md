# Manual de Operaciones — Horror Shorts Automation

> Manual de operación cotidiana del sistema. Léelo cuando quieras encender, apagar, monitorear o ajustar el sistema.

---

## 🎯 Sistema 100% automático — qué hace solo

Una vez configurado (ya está hecho), el sistema corre 24/7 con estos crons internos de n8n:

| Workflow | Frecuencia | Qué hace |
|---|---|---|
| **WF1_AIStoryGen** | Diario a las **06:00 UTC** | Genera 10 historias originales con GPT (~$0.20) |
| **WF3_Production** | **Cada 2 horas** | Toma 1 historia queued y produce el MP4 9:16 (~$0.005) |
| **WF4_Publish** | **Cada 2 horas** | Sube 1 MP4 rendered a YouTube (gratis, dentro de quota) |

**Resultado**: ~10 shorts publicados por día sin tu intervención. Coste ~$3-5/mes en OpenAI.

> ⏰ **Hora UTC vs hora local**: 06:00 UTC = 07:00 España invierno / 08:00 España verano / 01:00 México (CST) / 03:00 Argentina. Si quieres ajustar las horas a tu zona, edita el cron expression del nodo Schedule en cada workflow.

---

## 🔌 Encender el sistema (después de apagar el PC)

### Paso 1 — Abre Docker Desktop en Windows

1. Click en el icono de **Docker Desktop** en el menú de inicio
2. Espera 30-60 segundos a que el icono 🐳 deje de animarse en la barra de tareas (abajo derecha)
3. Cuando esté listo, el icono se queda fijo

### Paso 2 — Verifica que los contenedores arrancan solos

Los 5 contenedores tienen `restart: unless-stopped` en `docker-compose.yml`, así que **arrancan automáticamente** cuando arranca Docker Desktop.

Para confirmar, abre **Ubuntu** (WSL) y ejecuta:

```bash
cd ~/horror
docker compose ps
```

Debes ver los 5 contenedores en estado `Up` o `healthy`:
- `horror_postgres`
- `horror_redis`
- `horror_minio`
- `horror_n8n`
- `horror_render`

### Paso 3 — Si no arrancan solos

```bash
cd ~/horror
docker compose start
sleep 15
docker compose ps
```

### Paso 4 — Validación rápida (opcional)

```bash
curl http://localhost:8000/health
```

Debe responder: `{"status":"ok","version":"0.1.0"}`

A partir de aquí, los crons de n8n disparan automáticamente cuando lleguen sus horas. **No tienes que tocar nada más.**

---

## 🛑 Apagar el sistema (cuando quieras descansar tu PC)

### Opción A — Apagar Docker pero conservar configuración (recomendado)

1. Click derecho en el icono de Docker Desktop (barra de tareas)
2. **Quit Docker Desktop**

Esto para los contenedores. Tus datos (DB, MP4s en MinIO, workflows n8n, .env) se conservan intactos.

### Opción B — Apagar el PC directamente

Igual de seguro. Docker se cierra solo, los datos persisten.

### Opción C — Pause via terminal

```bash
cd ~/horror
docker compose stop
```

Para reanudar después:
```bash
docker compose start
```

> ⚠️ **NO uses** `docker compose down -v` — eso borra los volúmenes con tus datos.

---

## 📊 Monitoreo del sistema

### Ver estado actual

```bash
cd ~/horror
docker compose ps
curl http://localhost:8000/health
```

### Ver historias generadas hoy

```bash
docker compose exec postgres psql -U horror -d horror_shorts -c \
  "SELECT id, title, theme, fetched_at FROM app.story_candidates 
   WHERE fetched_at >= DATE_TRUNC('day', now() AT TIME ZONE 'UTC') 
   ORDER BY id DESC;"
```

### Ver shorts producidos hoy

```bash
docker compose exec postgres psql -U horror -d horror_shorts -c \
  "SELECT id, title, voice_id, duration_sec, status, created_at 
   FROM app.shorts 
   WHERE created_at >= DATE_TRUNC('day', now() AT TIME ZONE 'UTC') 
   ORDER BY id DESC;"
```

### Ver shorts subidos a YouTube hoy

```bash
docker compose exec postgres psql -U horror -d horror_shorts -c \
  "SELECT id, title, youtube_video_id, 
          'https://youtube.com/shorts/' || youtube_video_id AS url,
          published_at
   FROM app.shorts 
   WHERE status='published' 
     AND published_at >= DATE_TRUNC('day', now() AT TIME ZONE 'UTC') 
   ORDER BY id DESC;"
```

### Ver coste OpenAI acumulado

```bash
docker compose exec postgres psql -U horror -d horror_shorts -c \
  "SELECT 
     DATE(occurred_at) AS dia, 
     COUNT(*) AS llamadas, 
     ROUND(SUM(cost_usd)::numeric, 4) AS usd 
   FROM app.cost_ledger 
   WHERE service='openai' 
   GROUP BY DATE(occurred_at) 
   ORDER BY dia DESC LIMIT 7;"
```

### Ver rotación de voces

```bash
docker compose exec postgres psql -U horror -d horror_shorts -c \
  "SELECT arm_value AS voice, pulls, last_used 
   FROM app.bandit_arms 
   WHERE arm_type='voice' 
   ORDER BY pulls DESC;"
```

### Ver logs del render-service en vivo

```bash
docker compose logs -f render-service
```

(Ctrl+C para salir, no afecta al servicio)

---

## 🎛️ Ajustes comunes (sin tocar código)

Todo se configura vía SQL en la tabla `policy_params`.

### Cambiar cuántas historias se generan al día

```bash
# Subir a 10 historias/día
docker compose exec postgres psql -U horror -d horror_shorts -c \
  "UPDATE app.policy_params SET value='10'::jsonb WHERE key='stories_per_day';"
```

### Cambiar cuántos shorts se producen al día

```bash
# Bajar a 3 shorts/día
docker compose exec postgres psql -U horror -d horror_shorts -c \
  "UPDATE app.policy_params SET value='3'::jsonb WHERE key='max_shorts_per_day';"
```

### Cambiar cuántos shorts se suben a YouTube al día

```bash
# Subir a 6 (máximo recomendado por la quota gratuita de YouTube)
docker compose exec postgres psql -U horror -d horror_shorts -c \
  "UPDATE app.policy_params SET value='6'::jsonb WHERE key='max_uploads_per_day';"
```

### Quitar una voz que no te gusta

```bash
# Por ejemplo quitar Ximena
docker compose exec postgres psql -U horror -d horror_shorts -c \
  "UPDATE app.policy_params 
   SET value = value - 'es-ES-XimenaNeural' 
   WHERE key='voices';"
```

### Añadir una voz nueva

```bash
# Por ejemplo añadir Salomé colombiana
docker compose exec postgres psql -U horror -d horror_shorts -c \
  "UPDATE app.policy_params 
   SET value = value || '[\"es-CO-SalomeNeural\"]'::jsonb 
   WHERE key='voices';"
```

### Añadir un tema nuevo a la generación de historias

```bash
docker compose exec postgres psql -U horror -d horror_shorts -c \
  "UPDATE app.policy_params 
   SET value = value || '[\"objeto que aparece donde no debe\"]'::jsonb 
   WHERE key='story_themes';"
```

### Cambiar privacy por defecto al subir a YouTube

```bash
nano ~/horror/.env
# Cambia YOUTUBE_DEFAULT_PRIVACY a: public | unlisted | private

# Aplica:
cd ~/horror
docker compose up -d --force-recreate render-service
```

---

## 🛠️ Forzar ejecución manual de un workflow

Si quieres disparar un workflow sin esperar al cron:

### Opción A — Desde la UI de n8n

1. Abre `http://localhost:5678` (login admin / pass del bootstrap)
2. Click en el workflow que quieras ejecutar (WF1, WF3 o WF4)
3. Botón **Execute Workflow** abajo del centro
4. Verás los nodos pasar a verde uno por uno

### Opción B — Desde terminal con curl

```bash
KEY=$(grep ^RENDER_API_KEY= ~/horror/.env | cut -d= -f2)

# Generar 1 historia nueva
curl -X POST http://localhost:8000/stories/generate \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"count": 1, "language": "es"}' | jq .

# Producir 1 short (auto-elige el siguiente queued)
SHORT=$(curl -s "http://localhost:8000/produce/queue?limit=1" \
  -H "X-API-Key: $KEY" | jq '.[0].id')
curl -X POST http://localhost:8000/produce \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d "{\"candidate_id\": $SHORT}" --max-time 600 | jq .

# Subir 1 short a YouTube (auto-elige el siguiente rendered)
SHORT=$(curl -s "http://localhost:8000/publish/queue?limit=1" \
  -H "X-API-Key: $KEY" | jq '.[0].id')
curl -X POST http://localhost:8000/publish \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d "{\"short_id\": $SHORT}" --max-time 600 | jq .
```

---

## 💰 Control de costes

Tu sistema gasta automáticamente:

| Servicio | Coste por uso | Coste/mes (5 shorts/día) |
|---|---|---|
| OpenAI gpt-4o-mini | $0.001 por historia + $0.001 por guion | ~$0.30/mes |
| Azure Speech (free tier) | $0 (hasta 500k chars/mes) | $0 |
| Pixabay | $0 (ilimitado free) | $0 |
| YouTube Data API | $0 (10000 units/día gratis) | $0 |
| MinIO local | $0 | $0 |
| **TOTAL** | | **~$0.30/mes** |

Si subes a VPS Hetzner: +$8/mes = **~$8.30/mes total**.

### Ver coste actual de OpenAI

```bash
# Coste mes en curso
docker compose exec postgres psql -U horror -d horror_shorts -c \
  "SELECT ROUND(SUM(cost_usd)::numeric, 4) AS usd_este_mes 
   FROM app.cost_ledger 
   WHERE service='openai' 
     AND occurred_at >= DATE_TRUNC('month', now());"
```

### Ver saldo OpenAI restante

Abre: https://platform.openai.com/usage

---

## 🚨 Troubleshooting frecuente

### "El sistema no generó nada anoche"

```bash
# 1. Verifica que Docker está corriendo
docker compose ps

# 2. Verifica que los workflows están Active en n8n
# Abre http://localhost:5678 → Workflows → mira la columna "Active"

# 3. Mira logs por si hay algún error
docker compose logs --tail=100 render-service | grep -E "ERROR|Exception"

# 4. Verifica que el cron de n8n está disparando
docker compose logs n8n | grep -E "Schedule|cron"
```

### "Quiero parar todo temporalmente"

Despublica los workflows en n8n (toggle Active → Inactive). El sistema seguirá corriendo pero los crons no dispararán. Cuando quieras reanudar, vuelve a publicar.

### "OpenAI me está cobrando demasiado"

```bash
# Bajar a 2 historias/día
docker compose exec postgres psql -U horror -d horror_shorts -c \
  "UPDATE app.policy_params SET value='2'::jsonb WHERE key='stories_per_day';"
```

### "No quiero subir a YouTube por ahora"

Despublica solo WF4 en n8n. WF1 y WF3 seguirán generando historias y MP4s, pero no se subirán. Cuando quieras reanudar la subida, vuelve a publicar WF4.

### "Mi YouTube quota se agotó"

YouTube te da 10000 units/día. Cada upload cuesta 1600 = ~6 uploads/día. Si tu `max_uploads_per_day=5` no debería pasar, pero por si acaso:

1. Espera 24h (la quota se resetea automáticamente a las 00:00 PT, ~07:00 UTC)
2. O pide ampliación de quota a Google: https://support.google.com/youtube/contact/yt_api_form

### "El refresh_token de YouTube expiró"

Re-ejecuta el script OAuth:

```bash
cd ~/horror
CLIENT_ID=$(python3 -c "import json; print(json.load(open('youtube_creds.json'))['installed']['client_id'])")
CLIENT_SECRET=$(python3 -c "import json; print(json.load(open('youtube_creds.json'))['installed']['client_secret'])")
python3 scripts/youtube_oauth.py --client-id "$CLIENT_ID" --client-secret "$CLIENT_SECRET"
```

Pega el nuevo `YOUTUBE_REFRESH_TOKEN` en `.env` y `docker compose up -d --force-recreate render-service`.

---

## 🔐 Recordatorios de seguridad

- ⚠️ **NO compartas el contenido de `.env`** — contiene todas tus claves API
- ⚠️ **NO subas `.env` a git** (ya está en `.gitignore`)
- ⚠️ **Si sospechas que una clave se filtró**, regénerala inmediatamente:
  - OpenAI: https://platform.openai.com/api-keys → revoca la actual y crea nueva
  - YouTube: Google Cloud Console → Credenciales → "Reset secret"
  - Discord webhook: Server Settings → Integrations → Webhooks → eliminar y crear nuevo
- ⚠️ **Backup periódico de `youtube_creds.json` y `.env`** en un sitio seguro (no en git)

---

## 🚀 Migrar a VPS Hetzner cuando estés listo

Cuando quieras que el sistema corra 24/7 sin tu PC encendido:

1. Crea cuenta en https://www.hetzner.com/cloud
2. Crea un VPS CX22 ($8/mes, 4 vCPU, 8 GB RAM, 40 GB SSD)
3. SSH al VPS
4. Replica el setup (Docker, clone repo, copia `.env` y `youtube_creds.json`)
5. Migra el contenido de los volúmenes con `docker compose down`, `tar` los volúmenes, copiar al VPS, `docker compose up -d`

Si llegas a este punto, dime "vamos a migrar a VPS" en una sesión nueva y te guío.

---

## 📋 Comandos esenciales (cheatsheet)

```bash
# Estado del sistema
cd ~/horror && docker compose ps && curl http://localhost:8000/health

# Empezar
cd ~/horror && docker compose start

# Parar
cd ~/horror && docker compose stop

# Ver logs en vivo
cd ~/horror && docker compose logs -f render-service

# Métricas del día
docker compose exec postgres psql -U horror -d horror_shorts -c \
  "SELECT 'historias' AS m, COUNT(*)::text AS v FROM app.story_candidates WHERE fetched_at >= DATE_TRUNC('day', now() AT TIME ZONE 'UTC')
   UNION ALL SELECT 'shorts', COUNT(*)::text FROM app.shorts WHERE created_at >= DATE_TRUNC('day', now() AT TIME ZONE 'UTC')
   UNION ALL SELECT 'youtube', COUNT(*)::text FROM app.shorts WHERE status='published' AND published_at >= DATE_TRUNC('day', now() AT TIME ZONE 'UTC');"

# URLs útiles
echo "n8n:    http://localhost:5678"
echo "API:    http://localhost:8000/docs"
echo "MinIO:  http://localhost:9001"
```

---

**Última actualización**: 2026-05-23 (al cierre de sesión con WF1+WF3+WF4 activos y subiendo a YouTube)
