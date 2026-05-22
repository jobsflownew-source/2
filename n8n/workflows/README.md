# n8n Workflows

## Importacion

1. Abre n8n: `http://localhost:5678` (login con `N8N_BASIC_AUTH_USER` / `N8N_BASIC_AUTH_PASSWORD`).
2. Menu → **Workflows** → **Import from file** → selecciona el JSON.
3. Configura las credenciales (ver abajo).

## Credenciales necesarias

### `Postgres app`
- Type: **Postgres**
- Host: `postgres`
- Port: `5432`
- Database: el de `POSTGRES_DB` (`horror_shorts`)
- User: `POSTGRES_USER`
- Password: `POSTGRES_PASSWORD`
- SSL: `disable`

> Las claves de OpenAI / Discord van en variables de entorno (`.env`)
> y se referencian con `{{ $env.NOMBRE }}` desde los nodos HTTP.

## Workflows incluidos

### `WF1_AIStoryGen.json`
- **Trigger:** cron diario 06:00 UTC.
- Lee `policy_params` para saber cuantas historias generar (`stories_per_day`)
  y en que idioma (`default_language`).
- Si ya alcanzo la cuota diaria, omite y notifica skip a Discord.
- Si no, llama a `render-service:8000/stories/generate` con N solicitudes.
- Cada historia se inserta en `story_candidates` con `status='queued'`,
  `quality_score` y `horror_score` autoasignados por GPT.
- Notifica a Discord con resumen y coste total.

> Coste: ~$0.02 por historia con gpt-4o-mini.

### `WF3_Production.json`
- **Trigger:** cron cada 4 horas.
- Toma el siguiente candidato `queued` con mayor `quality_score`.
- Comprueba el limite diario (`policy_params.max_shorts_per_day`).
- Llama a `render-service:8000/produce`:
  1. Genera guion segmentado con GPT (cache si ya existe).
  2. Genera TTS por segmento (Edge-TTS gratis o Azure premium).
  3. Busca imagen stock por keywords (Pixabay/Unsplash/Pexels).
  4. Renderiza MP4 9:16 con Ken Burns + subtitulos ASS.
  5. Sube a MinIO y crea row en `shorts` con `status='rendered'`.
- Notifica a Discord con el video final o motivo de skip.

> Timeout del nodo HTTP: 600.000 ms (10 min) para tolerar renders pesados.

## Flujo end-to-end

```
06:00 UTC  → WF1_AIStoryGen
            → 5 historias en story_candidates (status='queued')

10:00 UTC  → WF3_Production
            → 1 historia -> 1 short renderizado en MinIO

14:00 UTC  → WF3_Production
            → 1 historia mas -> 1 short

18:00 UTC, 22:00 UTC, 02:00 UTC → idem (hasta max_shorts_per_day)
```

## Proximos workflows (no incluidos en este MVP)

- `WF4_Publish`: subida automatica a YouTube.
- `WF5_Analytics`: recoleccion de metricas YouTube Analytics.
- `WF6_Optimizer`: ajuste automatico de themes/voces segun rendimiento.
