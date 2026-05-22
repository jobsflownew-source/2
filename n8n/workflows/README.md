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
- Database: el de `POSTGRES_DB`
- User: `POSTGRES_USER`
- Password: `POSTGRES_PASSWORD`
- SSL: `disable`
- Schema: `app`

### `Reddit Basic Auth (client_id:client_secret)`
- Type: **Basic Auth**
- Username: tu `REDDIT_CLIENT_ID`
- Password: tu `REDDIT_CLIENT_SECRET`

> **Nota:** las claves de OpenAI / Reddit / Discord van en variables de entorno
> (`.env`) y se referencian con `{{ $env.NOMBRE }}` desde los nodos HTTP.
> Si prefieres credenciales dedicadas en n8n, crealas y enlazalas desde cada nodo.

## Workflows incluidos

### `WF1_Ingestion_Reddit.json`
- Trigger: cron cada 6 horas.
- Saca top semanal de `r/nosleep`, filtra (NSFW, longitud, score>500), upsert en DB.
- Notifica a Discord con stats.

### `WF2_Curation_GPT.json`
- Trigger: cron diario a las 07:00.
- Toma 10 candidatos `new` con mayor score.
- Evalua con `gpt-4o-mini` (horror_score, quality_score, shorts_potential).
- Promueve a `queued` los que superan los thresholds en `policy_params`.
- Registra coste en `cost_ledger`.

### `WF3_Production.json`
- Trigger: cron cada 4 horas.
- Toma el siguiente candidato `queued` con mayor `quality_score`.
- Comprueba el limite diario (`policy_params.max_shorts_per_day`).
- Llama a `render-service:8000/produce`:
  1. Genera guion con GPT (cache si ya existe en `scripts`).
  2. Genera TTS por segmento (Edge-TTS gratis).
  3. Busca imagen stock por keywords (Pixabay/Unsplash/Pexels).
  4. Renderiza MP4 9:16 con Ken Burns + subs ASS.
  5. Sube a MinIO y crea row en `shorts` con `status='rendered'`.
- Notifica a Discord con el video final o con el motivo de skip.

> Timeout del nodo HTTP: **600000 ms (10 min)** para tolerar renders pesados.

## Proximos workflows (no incluidos en este MVP, ver SETUP.md)

- `WF4_Publish`: subida a YouTube.
- `WF5_Analytics`: recoleccion de metricas.
