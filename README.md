# Horror Shorts Automation — MVP

Sistema 100% automático para generar YouTube Shorts de historias de terror desde Reddit (`r/nosleep`).

## Coste objetivo: ~$15 USD/mes

| Componente | Coste mensual |
|---|---|
| VPS Hetzner CX22 (4 vCPU, 8 GB RAM) | $8 |
| Cloudflare R2 storage (50 GB) | $1 |
| OpenAI gpt-4o-mini (~150 shorts/mes) | $5 |
| Azure TTS / Edge-TTS | $0 (free tier) |
| Reddit / Unsplash / Pixabay APIs | $0 |
| YouTube Data API | $0 |
| Discord webhooks | $0 |
| **TOTAL** | **~$14/mes** |

## Pipelines

| Workflow | Función |
|---|---|
| `WF1_Ingestion` | Reddit → DB de candidatos (cada 6h) |
| `WF2_Curation` | Score con GPT → cola "to-produce" (diario) |
| `WF3_Production` | Guion → TTS → Imágenes → Video |
| `WF4_Publish` | Sube a YouTube + notifica Discord |
| `WF5_Analytics` | Recoge métricas (cada 12h) |

## Arranque rápido

```bash
cp .env.example .env
# Edita .env con tus credenciales
docker compose up -d
# n8n: http://localhost:5678
# Render API: http://localhost:8000/docs
```

Ver [`docs/SETUP.md`](docs/SETUP.md) para guía completa.

## Estructura

```
.
├── docker-compose.yml
├── .env.example
├── db/
│   └── init.sql              # Schema PostgreSQL
├── render-service/           # Microservicio FastAPI (TTS, imágenes, video)
│   ├── app/
│   ├── Dockerfile
│   └── requirements.txt
├── n8n/
│   └── workflows/            # JSON exportables a n8n
├── docs/
│   └── SETUP.md
└── scripts/
    └── bootstrap.sh
```
