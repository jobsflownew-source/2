# Horror Shorts Automation — MVP (100% AI)

Sistema 100% automático para generar YouTube Shorts de historias originales de terror, **escritas por IA** (sin Reddit, sin problemas de copyright).

## Coste objetivo: ~$15 USD/mes

| Componente | Coste mensual |
|---|---|
| VPS Hetzner CX22 (4 vCPU, 8 GB RAM) | $8 |
| Storage (MinIO local en VPS) | $0 |
| OpenAI GPT-4o-mini (~150 historias + guiones/mes) | $5 |
| Azure TTS / Edge-TTS | $0 (free tier o gratis) |
| Unsplash / Pixabay / Pexels APIs | $0 |
| YouTube Data API | $0 |
| Discord webhooks | $0 |
| **TOTAL** | **~$13/mes** |

## Pipelines

| Workflow | Función |
|---|---|
| `WF1_AIStoryGen` | GPT genera N historias originales/día (cron diario 06:00) |
| `WF3_Production` | Guion segmentado → TTS → Imágenes → MP4 (cron 4h) |
| `WF4_Publish` | Sube a YouTube + notifica Discord *(próximo)* |
| `WF5_Analytics` | Recoge métricas YouTube *(próximo)* |

## Por qué IA en lugar de Reddit

- ✅ **Cero riesgo legal** — todas las historias son obras originales
- ✅ **Control total** — puedes ajustar tema, tono, longitud, idioma
- ✅ **Diversidad infinita** — 15 themes × 15 settings × 5 tones = 1125 combinaciones únicas
- ✅ **Más barato** — ~$0.02 por historia con gpt-4o-mini
- ✅ **Cumple políticas YouTube** — sin reclamos de autores, sin copyright

## Arranque rápido

```bash
cp .env.example .env
# Edita .env: solo necesitas OPENAI_API_KEY como obligatoria
docker compose up -d
# n8n: http://localhost:5678
# Render API: http://localhost:8000/docs
```

Ver [`docs/SETUP.md`](docs/SETUP.md) para guía completa paso a paso.

## Estructura

```
.
├── docker-compose.yml
├── .env.example
├── db/
│   ├── init.sql                       # Schema PostgreSQL completo
│   └── migrations/                    # Migraciones para upgrades
├── render-service/                    # Microservicio FastAPI
│   ├── app/
│   │   ├── main.py                    # Endpoints REST
│   │   ├── story_gen.py               # Generador de historias IA
│   │   ├── script_gen.py              # Adapta historia -> guion segmentado
│   │   ├── pipeline.py                # Orquesta produccion
│   │   ├── tts.py / tts_azure.py      # Edge-TTS + Azure
│   │   ├── images.py                  # Stock (Pixabay/Unsplash/Pexels)
│   │   ├── video.py                   # FFmpeg + Ken Burns + subs
│   │   ├── db.py                      # PostgreSQL pool
│   │   └── storage.py                 # MinIO S3
│   ├── Dockerfile
│   └── requirements.txt
├── n8n/
│   └── workflows/
│       ├── WF1_AIStoryGen.json
│       └── WF3_Production.json
├── docs/
│   ├── SETUP.md
│   └── ARCHITECTURE.md
└── scripts/
    ├── bootstrap.sh
    ├── test_render.sh                 # Smoke test TTS + stock images
    └── test_produce.sh                # Smoke test end-to-end
```
