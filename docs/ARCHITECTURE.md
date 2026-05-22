# Arquitectura — Horror Shorts Automation (100% AI)

## Diagrama de alto nivel

```
                         +-------------------+
   Cron 06:00 UTC ------->  WF1_AIStoryGen  |--+
                         +-------------------+  |
                                                 v
                            +-------------------+
                            |  story_candidates |
                            |  (status=queued) |
                            +-------------------+
                                    ^
                                    |
   Cron 4h     -------------> +-------------------+
                              |  WF3_Production   |
                              +-------------------+
                                    |
                                    v
                            +-------------------+
                            |  Render Service   |
                            |  FastAPI          |
                            |  - /stories/gen  <-- llamado por WF1
                            |  - /produce      <-- llamado por WF3
                            |  - /tts /images   |
                            |  - /script /render|
                            +-------------------+
                                    |
                                    v
                            +-------------------+
                            |  MinIO (S3)       |
                            |  + Postgres app   |
                            +-------------------+

   APIs externas:
   - OpenAI Chat (api.openai.com) — REQUERIDO
   - Azure / Edge TTS — fallback automatico
   - Unsplash / Pixabay / Pexels — opcional
   - YouTube Data API v3 (futuro WF4)
   - Discord webhooks
```

## Decisiones de diseno

### Por que IA en lugar de Reddit
- **Cero riesgo legal**: las historias son obras originales generadas en cada ejecucion.
- **Cero dependencia externa critica**: si Reddit cambia su API, no nos afecta.
- **Control creativo total**: themes, settings, tones configurables en `policy_params`.
- **Coste menor**: ~$0.02 por historia, vs costes potenciales de licencias.
- **Escalabilidad**: 1125 combinaciones theme×setting×tone = meses sin repetir.

### Por que separar n8n de Postgres y del render-service
- **n8n** orquesta y reintenta, pero no debe hacer trabajo pesado (CPU, render, GPT).
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

### Modelo de datos
- `story_candidates`: pipeline de historias (multi-source: ai_generated/reddit/public_domain/manual).
- `scripts`: artefacto inmutable por candidate_id + idioma.
- `assets`: piezas reutilizables (imagen N puede usarse en short A y B traducido).
- `shorts`: el video final, con `parent_short_id` para versiones traducidas.
- `metrics_hourly`: time-series append-only (futuro WF5).
- `cost_ledger`: ledger append-only para tracking auditable de costes.
- `policy_params` + `bandit_arms`: configuracion + estadisticas para
  optimizacion automatica (futuro WF6).

### Reintentos e idempotencia
- En **n8n**: nodos HTTP con `retry.enabled = true` y backoff.
- En **WF1**: hash MD5 del texto evita duplicados (constraint `(source, external_id)`).
- En **WF3**: re-ejecutar con mismo candidate_id reusa el guion ya generado (sin double-charge OpenAI).
- En **/produce**: short_id consistente, llamadas idempotentes.

### Seguridad
- `render-service` solo accesible dentro de la red Docker, expuesto a `localhost`.
- Auth con `X-API-Key` (header) entre n8n y render-service.
- Postgres no expone puerto publico; n8n y render-service hablan via DNS interno.
- En produccion: ponle Caddy/Traefik delante con TLS y restringe IPs.

## Limites diarios en MVP

| Recurso | Limite | Por que |
|---|---|---|
| Historias generadas/dia | 5 | Configurable en `policy_params.stories_per_day` |
| Shorts/dia (render) | 5 | Configurable en `policy_params.max_shorts_per_day` |
| Coste OpenAI/dia | <$0.30 | 5 historias + 5 guiones gpt-4o-mini |
| TTS chars/dia | ~15.000 | Edge-TTS ilimitado, Azure free 500k/mes |
| Storage assets | ~1 GB/dia | 5 shorts × 200 MB |

## Escalado

Cuando este MVP pegue (>1M views/mes), migra a:
- VPS dedicado con GPU (RTX 4090) o RunPod serverless para imagen IA propia.
- n8n en modo `queue` con workers Redis.
- Postgres gestionado (Neon/Supabase) con backups automaticos.
- Cloudflare R2 (egress gratis, mejor para servir miniaturas/preview).
- Multi-canal: 1 proyecto GCP por canal para no compartir cuota YouTube.
- Cambiar a `gpt-4o` (mas calidad) para historias top: ~$0.20 por historia.
