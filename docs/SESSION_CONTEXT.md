# Horror Shorts Automation — Session Context

> Documento maestro para retomar el proyecto en cualquier sesión nueva de IA o desarrollador.
> **Última actualización**: 2026-05-23 al cerrar la jornada de setup completo + construcción de WF4.

---

## 🎯 Qué es este proyecto

Sistema 100% automatizado para generar **YouTube Shorts de terror** usando:
- **GPT-4o-mini** como guionista (genera historias originales — sin Reddit, sin copyright)
- **Azure Speech / Edge-TTS** con rotación de 6 voces neurales en español
- **Pixabay** para imágenes stock + placeholder cinematográfico de fallback
- **FFmpeg + MoviePy** para render 9:16 con Ken Burns y subtítulos quemados
- **YouTube Data API v3** para subida automática (WF4)
- **n8n** como orquestador con cron triggers

**Coste objetivo**: ~$15/mes (VPS $8 + OpenAI $5-7).

---

## 📊 Estado actual del sistema

### Arquitectura desplegada localmente (en WSL2 Ubuntu del usuario)

```
Docker Compose stack:
├── postgres (PostgreSQL 16)        ← schema 'app' con 10 tablas
├── redis (Redis 7)                 ← cache (preparado para colas futuras)
├── minio (S3-compatible)           ← almacena MP4s, audios, imágenes
├── n8n (latest, v2.21.7)           ← orquestador con 3 workflows
└── render-service (FastAPI Python) ← microservicio con FFmpeg
```

### Workflows n8n (3 importados, 2 publicados, 1 pendiente)

| Workflow | Cron | Estado | Función |
|---|---|---|---|
| `WF1_AIStoryGen` | Daily 06:00 UTC | ✅ Published | Genera N historias con GPT |
| `WF3_Production` | Cada 4h | ✅ Published | Produce 1 short (guion + TTS + imágenes + render) |
| `WF4_Publish` | Cada 6h | ⏳ Código listo, pendiente importar | Sube short a YouTube |

### APIs externas configuradas

| API | Estado | Notas |
|---|---|---|
| OpenAI | ✅ Configurada | $5 prepaid balance, gpt-4o-mini |
| Azure Speech | ✅ Configurada | F0 free tier (500k chars/mes) |
| Pixabay | ✅ Configurada | API key gratuita |
| Discord webhook | ⚠️ Inválido (legacy domain) | Usar `discord.com` (no `discordapp.com`); los nodos toleran fallo |
| YouTube Data API v3 | ⏳ Pendiente | Falta crear proyecto GCP + OAuth (FASE 1 docs/SETUP.md sección 12) |

### Resultado validado al cierre de sesión

- ✅ 10+ historias generadas automáticamente con GPT
- ✅ 5+ shorts MP4 producidos end-to-end (visibles en MinIO)
- ✅ Voces rotando entre 6 del banco curado vía `bandit_arms`
- ✅ Coste real consumido: <$0.30 OpenAI hoy
- ⏳ Subida a YouTube todavía manual (descargar MP4 + subir desde YouTube Studio)

---

## 🗂️ Estructura del repo

Repo: `https://github.com/jobsflownew-source/2`
Rama activa con todos los fixes: **`fix/story-min-words-and-retry`** (PR #4 abierto)

```
2/
├── docker-compose.yml              # Stack 5 servicios
├── .env.example                    # Plantilla de configuración
├── README.md                       # Resumen ejecutivo
├── db/
│   ├── init.sql                    # Schema completo (story_candidates, scripts, shorts, etc.)
│   └── migrations/
│       ├── 002_ai_story_source.sql # rename reddit→story_candidates
│       ├── 003_curated_voices.sql  # banco de 6 voces para terror
│       └── 004_max_uploads_per_day.sql  # quota YouTube
├── render-service/
│   ├── Dockerfile
│   ├── requirements.txt            # +google-auth, google-api-python-client
│   └── app/
│       ├── main.py                 # FastAPI: /tts /render /script /produce /publish
│       ├── config.py               # Settings con todas las vars
│       ├── db.py                   # Acceso PostgreSQL con pool
│       ├── story_gen.py            # Generador de historias con GPT
│       ├── script_gen.py           # Adapta historia a guion segmentado
│       ├── pipeline.py             # Orquestador candidate→MP4
│       ├── tts.py + tts_azure.py   # Azure primario + Edge fallback (provider auto)
│       ├── images.py               # Pixabay/Unsplash/Pexels
│       ├── images_placeholder.py   # Cinematográfico cuando no hay APIs
│       ├── video.py                # FFmpeg + Ken Burns + subs ASS
│       ├── storage.py              # MinIO S3
│       └── youtube.py              # Cliente YouTube Data API v3 (NEW)
├── n8n/workflows/
│   ├── WF1_AIStoryGen.json         # Generación diaria
│   ├── WF3_Production.json         # Render cada 4h
│   ├── WF4_Publish.json            # Subida a YouTube cada 6h (NEW)
│   └── README.md
├── scripts/
│   ├── bootstrap.sh                # Setup inicial
│   ├── test_aistory.sh             # Genera 1 historia para test
│   ├── test_produce.sh             # Produce 1 short para test
│   ├── test_render.sh              # Smoke test
│   ├── produce_all.sh              # Batch producir todos los queued (no-interactivo)
│   └── youtube_oauth.py            # Obtener refresh_token (1 vez)
└── docs/
    ├── SETUP.md                    # Guía paso a paso completa
    ├── ARCHITECTURE.md             # Diseño del sistema
    └── SESSION_CONTEXT.md          # Este archivo
```

---

## 🔧 Decisiones de diseño tomadas

1. **100% IA generative, sin Reddit**: cero riesgo legal de copyright + control total del estilo
2. **Provider auto en TTS**: Azure si está configurada, Edge-TTS como fallback automático — el sistema nunca falla por TTS
3. **Placeholder cinematográfico**: si fallan todas las APIs de imágenes, el sistema genera fondos oscuros con vignette/halo localmente — el render NUNCA aborta
4. **Rotación de voces vía bandit_arms**: round-robin equitativo. Cuando haya datos de retención YouTube → Thompson sampling
5. **Idempotencia**: re-ejecutar `/produce` con mismo candidate_id reusa el guion (no double-charge OpenAI)
6. **Cuotas diarias por policy_params**: `stories_per_day=5`, `max_shorts_per_day=5`, `max_uploads_per_day=5` — protección anti-coste
7. **Auto-retry en story_gen**: si GPT genera <500 palabras, reintenta con prompt expansivo
8. **typeValidation: loose en nodos IF**: necesario en n8n 2.21.7 porque Postgres devuelve INT como string
9. **specifyBody: json explícito**: en n8n 2.21.7 v4.2, sin esto el body se envía vacío
10. **method: POST explícito**: en n8n 2.21.7 v4.2, no autodetecta del sendBody=true

---

## 💾 Modelo de datos clave

### Tabla `story_candidates`
Multi-source: `ai_generated` | `reddit` | `public_domain` | `manual`.
Estados: `new` → `scored` → `queued` → `produced` (o `rejected`/`blacklisted`).

### Tabla `shorts`
Estados: `pending` → `rendering` → `rendered` → `uploading` → `published` (o `failed`).
Campos clave: `youtube_video_id`, `voice_id`, `final_video_url`, `duration_sec`.

### Tabla `policy_params` (configuración runtime)
- `voices`: JSON array de 6 voces curadas
- `story_themes`: 15 temas
- `story_settings`: 15 settings
- `story_tones`: 5 tonos
- `stories_per_day`, `max_shorts_per_day`, `max_uploads_per_day`
- `default_language`: "es"

### Tabla `bandit_arms`
`(arm_type, arm_value)` con `pulls` y `rewards_sum`. Usado para round-robin de voces.

### Tabla `cost_ledger`
Ledger append-only con cada llamada a OpenAI/Azure/YouTube. Auditable.

---

## 📝 Cambios pendientes (open todos)

### Inmediato (sesión actual o próxima)
- [ ] **FASE 1 YouTube setup**: crear proyecto Google Cloud, habilitar YouTube Data API v3, crear OAuth Desktop Client. Detalles en `docs/SETUP.md` (próxima sección a documentar).
- [ ] **FASE 2 OAuth flow**: ejecutar `python3 scripts/youtube_oauth.py --client-id X --client-secret Y` para obtener `refresh_token`.
- [ ] Pegar `YOUTUBE_CLIENT_ID/SECRET/REFRESH_TOKEN` en `.env` y `docker compose up -d --force-recreate render-service`.
- [ ] Aplicar `db/migrations/004_max_uploads_per_day.sql`.
- [ ] Importar `WF4_Publish.json` en n8n y publicarlo.
- [ ] Test: `curl -X POST localhost:8000/publish -d '{"short_id": N, "privacy": "unlisted"}'`.

### Mejoras de calidad (cuando se valide audiencia)
- [ ] Música de fondo CC0 mezclada con narración (-22 LUFS bg, -16 LUFS voz).
- [ ] Imágenes IA propias con Replicate SDXL o Stable Diffusion local (~$5/mes extra).
- [ ] Animaciones más dramáticas (Ken Burns con curvas easing).
- [ ] Miniaturas custom con Pillow + saliencia OpenCV.

### WF5 - Analytics (siguiente gran bloque)
- [ ] Recolección horaria de views/retention/CTR de YouTube Analytics API.
- [ ] Tabla `metrics_hourly` (ya existe en schema).
- [ ] Dashboard Grafana con `v_daily_costs` y `shorts_performance_24h`.

### WF6 - Optimizer
- [ ] Thompson sampling sobre `bandit_arms.rewards_sum` con datos de retention.
- [ ] Auto-ajuste de `stories_per_day` basado en performance histórica.

### WF7 - Comentarios
- [ ] `commentThreads.list` + clasificación con GPT (`pregunta`/`elogio`/`crítica`/`spam`).
- [ ] Auto-respuesta a `pregunta` y `elogio` con personalidad fija.

### WF8 - Multi-idioma
- [ ] Cuando un short pasa cierto threshold (ej. 50k views), generar versiones es/en/pt/fr.
- [ ] Reusar imágenes, regenerar TTS y subs.

### Producción real
- [ ] Migrar a VPS Hetzner CX22 ($8/mes) para 24/7 sin PC encendido.
- [ ] Setup HTTPS con Caddy/Traefik.
- [ ] Backup automático de DB (cron + S3).

---

## 🔐 Secretos y dónde están

⚠️ **NO compartir el contenido del `.env` en chats**.

Localmente en `~/horror/.env` el usuario tiene:
- `POSTGRES_PASSWORD`, `MINIO_ROOT_PASSWORD`, `N8N_BASIC_AUTH_PASSWORD`, `RENDER_API_KEY` (auto-generados por bootstrap.sh)
- `OPENAI_API_KEY`
- `AZURE_SPEECH_KEY` + `AZURE_SPEECH_REGION`
- `PIXABAY_API_KEY`
- `DISCORD_WEBHOOK_URL` ⚠️ Pendiente regenerar (apareció en chat anterior)
- Pendiente: `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`

---

## 🐛 Bugs encontrados y arreglados (PR #4)

Lista para no repetirlos en deploys futuros:

| # | Bug | Fix commit |
|---|---|---|
| 1 | story_too_short en WF1 (GPT-4o-mini genera <800 palabras) | Bajar mínimo a 500 + auto-retry expansivo |
| 2 | test_produce.sh leía `.candidate_id` del endpoint /produce/queue (devuelve `.id`) | jq fallback `.[0].id // .[0].candidate_id` |
| 3 | Edge-TTS bloqueado por Microsoft (WSServerHandshakeError) | provider="auto" + actualizar edge-tts a 7.2.8 |
| 4 | "No image found for segment X" si APIs stock fallan/vacías | Placeholder cinematográfico con Pillow |
| 5 | Una sola voz monótona | 6 voces curadas + rotación bandit |
| 6 | n8n IF rechaza "0" como string (typeValidation strict) | Cambiar a "loose" |
| 7 | n8n bloquea $env.X access | N8N_BLOCK_ENV_ACCESS_IN_NODE=false + pasar vars |
| 8 | Discord URL vacía rompe workflow | onError: continueRegularOutput + continueOnFail |
| 9 | WF3 query usaba `app.reddit_candidates` (tabla renombrada) | Cambiar a `app.story_candidates` |
| 10 | n8n HTTP node hace GET por default → 405 al render-service | method: POST explícito |
| 11 | n8n HTTP node con sendBody envía body vacío | specifyBody: "json" en lugar de contentType |

---

## 🚀 Comandos esenciales para cualquier sesión nueva

### Verificar estado del sistema (ejecutar primero)

```bash
cd ~/horror
docker compose ps
curl http://localhost:8000/health
git log --oneline -5
```

### Diagnóstico rápido

```bash
# Inventario de la DB
docker compose exec postgres psql -U horror -d horror_shorts -c \
  "SELECT 'candidates_total' AS m, COUNT(*)::text AS v FROM app.story_candidates
   UNION ALL SELECT 'queued', COUNT(*)::text FROM app.story_candidates WHERE status='queued'
   UNION ALL SELECT 'shorts_total', COUNT(*)::text FROM app.shorts
   UNION ALL SELECT 'shorts_rendered', COUNT(*)::text FROM app.shorts WHERE status='rendered'
   UNION ALL SELECT 'shorts_published', COUNT(*)::text FROM app.shorts WHERE status='published'
   UNION ALL SELECT 'cost_today_usd', ROUND(COALESCE(SUM(cost_usd),0)::numeric, 4)::text 
                 FROM app.cost_ledger WHERE occurred_at >= DATE_TRUNC('day', now() AT TIME ZONE 'UTC');"

# Ver el último error en el render-service
docker compose logs --tail=50 render-service | grep -E "ERROR|Exception"
```

### Forzar generación / producción manual

```bash
KEY=$(grep ^RENDER_API_KEY= .env | cut -d= -f2)

# 1 historia nueva
curl -X POST http://localhost:8000/stories/generate -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" -d '{"count": 1, "language": "es"}' | jq .

# 1 short del próximo queued
SHORT=$(curl -s "http://localhost:8000/produce/queue?limit=1" -H "X-API-Key: $KEY" | jq '.[0].id')
curl -X POST http://localhost:8000/produce -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" -d "{\"candidate_id\": $SHORT}" --max-time 600 | jq .

# 1 upload manual a YouTube (cuando WF4 esté listo)
SHORT=$(curl -s "http://localhost:8000/publish/queue?limit=1" -H "X-API-Key: $KEY" | jq '.[0].id')
curl -X POST http://localhost:8000/publish -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" -d "{\"short_id\": $SHORT, \"privacy\": \"unlisted\"}" \
  --max-time 600 | jq .
```

### Apagar / encender el sistema

```bash
# Apagar (conserva datos)
docker compose stop

# Encender
docker compose start
sleep 10
curl http://localhost:8000/health

# Reset completo (BORRA TODO, solo en emergencia)
docker compose down -v   # ⚠️
```

---

## 🎬 Cómo continuar en una sesión nueva de IA

### Paso 1 — Pega este prompt al inicio de la conversación

```
Estoy retomando un proyecto que ya tengo en marcha: un sistema 100%
automatizado de generación de YouTube Shorts de terror con n8n + GPT +
Azure TTS + FFmpeg. El repo está en GitHub:

  https://github.com/jobsflownew-source/2

La rama con todos los últimos fixes es `fix/story-min-words-and-retry`
(PR #4). Por favor lee primero:

  1. docs/SESSION_CONTEXT.md  (estado completo del proyecto)
  2. docs/ARCHITECTURE.md     (diseño)
  3. docs/SETUP.md            (guía paso a paso)

Mi entorno local:
- Windows con WSL2 Ubuntu
- Docker Desktop con integración WSL
- Repo clonado en ~/horror
- 5 contenedores corriendo: postgres, redis, minio, n8n, render-service
- WF1 + WF3 publicados y funcionando automáticamente
- WF4 (subida YouTube) construido pero pendiente de OAuth setup

Quiero continuar con: [DESCRIBE LO QUE QUIERES HACER]

Por favor revisa SESSION_CONTEXT.md y dame el siguiente paso.
```

### Paso 2 — Tareas típicas de continuación

| Quiero... | Pídele a la IA... |
|---|---|
| Completar setup YouTube | "Llévame paso a paso por la FASE 1 YouTube de SESSION_CONTEXT.md" |
| Ver mis shorts producidos | "Genera los SQL queries para ver el estado actual de mi DB" |
| Cambiar voces / themes / tonos | "Necesito modificar policy_params.voices para quitar Ximena" |
| Iterar calidad de historias | "El prompt de story_gen.py es muy florido, hazlo más conversacional" |
| Migrar a VPS Hetzner | "Estoy listo para migrar a VPS, guíame por los pasos" |
| Construir WF5 Analytics | "Ya tengo varios shorts publicados, vamos con WF5" |

---

## 🔗 Enlaces útiles

- **Repo**: https://github.com/jobsflownew-source/2
- **PR #4 (latest fixes)**: https://github.com/jobsflownew-source/2/pull/4
- **OpenAI dashboard**: https://platform.openai.com/usage
- **Azure portal**: https://portal.azure.com
- **Pixabay docs**: https://pixabay.com/api/docs/
- **YouTube Data API quota**: https://console.cloud.google.com/apis/api/youtube.googleapis.com/quotas
- **n8n docs**: https://docs.n8n.io/

---

## 📞 Si la nueva IA no entiende el contexto

Pídele que ejecute estos comandos en tu Ubuntu y te explique qué ve:

```bash
cd ~/horror
git log --oneline -10
docker compose ps
ls -la n8n/workflows/
ls -la render-service/app/
cat docs/SESSION_CONTEXT.md | head -100
```

Con esos outputs cualquier IA tiene contexto suficiente para seguir.

---

**Última verificación de estado**: 2026-05-23
**Total commits en PR #4**: 12+
**Total líneas modificadas**: ~3500
**Días de setup**: 1 (todo el día épico de troubleshooting)
**Resultado**: Sistema 100% automático funcionando local; falta solo OAuth YouTube para 100% end-to-end.
