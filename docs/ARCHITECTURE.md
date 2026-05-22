# Arquitectura — Horror Shorts Automation

## Diagrama de alto nivel

```
                         +-------------------+
   Cron 6h --------------->  WF1_Ingestion  |--+
                         +-------------------+  |
                                                 v
   Cron diario 07:00 ---->  +-------------------+
                            |  WF2_Curation    |--+
                            +-------------------+  |
                                                    v
                            +-------------------+
                            |   PostgreSQL     |
                            |  (app schema)    |
                            +-------------------+
                                    ^
                                    |
                            +-------------------+
                            |  Render Service  |
                            |  FastAPI         |
                            |  - /tts          |
                            |  - /images       |
                            |  - /render       |
                            +-------------------+
                                    |
                                    v
                            +-------------------+
                            |  MinIO (S3)      |
                            +-------------------+

   APIs externas:
   - Reddit OAuth (oauth.reddit.com)
   - OpenAI Chat (api.openai.com)
   - Azure / Edge TTS
   - Unsplash / Pixabay / Pexels
   - YouTube Data API v3 (futuro WF4)
   - Discord webhooks
```

## Decisiones de diseno

### Por que separar n8n de Postgres y del render-service
- **n8n** orquesta y reintenta, pero no debe hacer trabajo pesado (CPU, render).
- **render-service** es un microservicio Python aislado con FFmpeg, controlable por API key.
- **Postgres** sirve a n8n (schema `n8n`) y a la app (schema `app`) sin conflictos.

### Por que MinIO local en lugar de S3
En MVP `$15/mes`, almacenamiento local en el VPS es suficiente y gratis.
Cuando escales: reemplaza endpoint MinIO por **Cloudflare R2** (sin cambios en codigo)
o AWS S3, ya que la API es compatible.

### Por que Edge-TTS como primario
- Cero coste, sin clave.
- Voces neurales identicas a Azure (mismo backend Microsoft).
- Como fallback si Edge falla: Azure free tier (500k chars/mes).

### Por que separar WF1 y WF2
- WF1 (ingesta) corre 4×/dia, llamadas baratas (Reddit gratis).
- WF2 (curation con GPT) corre 1×/dia, controla el coste OpenAI.
- Si OpenAI cae, la ingesta sigue funcionando.

### Modelo de datos
- `reddit_candidates`: pipeline de candidatos con `status` como state machine.
- `scripts`: artefacto inmutable por candidato + idioma.
- `assets`: piezas reutilizables (imagen N puede usarse en short A y B traducido).
- `shorts`: el video final publicado, con `parent_short_id` para versiones traducidas.
- `metrics_hourly`: time-series append-only.
- `cost_ledger`: ledger append-only para tracking de costes (auditable).
- `policy_params` + `bandit_arms`: tabla de configuracion + estadisticas para
  optimizacion automatica (futura WF6).

### Reintentos e idempotencia
- En **n8n**: cada nodo HTTP tiene `retry.enabled = true` con backoff.
- En **DB**: upserts con `ON CONFLICT` para WF1 (Reddit puede repetir posts entre fetches).
- En **render**: `short_id` como clave; el endpoint puede llamarse N veces sin duplicar.

### Seguridad
- `render-service` solo accesible dentro de la red Docker, expuesto a `localhost`.
- Auth con `X-API-Key` (header) entre n8n y render-service.
- Postgres no expone puerto publico; n8n y render-service hablan via DNS interno (`postgres:5432`).
- En produccion: ponle un Caddy/Traefik delante con TLS y restringe IPs si abres puertos.

## Limites diarios en MVP

| Recurso | Limite | Por que |
|---|---|---|
| Shorts/dia (futuro WF4) | 5 | YouTube quota: 1 upload = 1600 units, daily quota = 10000 |
| Candidates evaluados/dia | 10 | Controla coste OpenAI a ~$0.05/dia |
| TTS chars/dia | ~50000 | Azure free 500k/mes / 30 dias |
| Storage assets | ~1 GB/dia | Asumiendo 5 shorts × 200 MB |

## Escalado

Cuando este MVP pegue (>1M views/mes), migra a:
- VPS dedicado con GPU (RTX 4090) o RunPod serverless para imagen IA.
- n8n en modo `queue` con workers Redis.
- Postgres gestionado (Neon/Supabase) con backups automaticos.
- Cloudflare R2 (egress gratis, mejor para servir miniaturas/preview).
- Multi-canal: 1 proyecto GCP por canal para no compartir cuota YouTube.
