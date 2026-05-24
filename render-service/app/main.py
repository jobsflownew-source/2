"""FastAPI app: endpoints para TTS, busqueda de imagenes y render de short."""
from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path
from typing import Optional

import httpx
import structlog
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import db
from .config import settings
from .images import download_image, find_best_image
from .pipeline import produce_short
from .script_gen import generate_script
from .security import require_api_key
from .storage import get_storage
from .story_gen import (DEFAULT_SETTINGS, DEFAULT_THEMES, DEFAULT_TONES,
                        generate_story)
from .tts import list_voices, synthesize
from .video import Segment, get_duration_sec, render_short

logging.basicConfig(level=settings.log_level)
log = structlog.get_logger()

app = FastAPI(
    title="Horror Shorts Render Service",
    version="0.1.0",
    description="TTS, image search and 9:16 video rendering for YouTube Shorts.",
)


WORKSPACE = Path(settings.workspace_dir)
WORKSPACE.mkdir(parents=True, exist_ok=True)


# ---------- Health ----------
@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


# ---------- TTS ----------
class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=8000)
    voice: Optional[str] = None
    provider: str = "edge"  # edge | azure
    upload: bool = True
    object_prefix: str = "tts"


class TTSResponse(BaseModel):
    storage_url: Optional[str]
    local_path: str
    duration_sec: float
    word_boundaries: list[dict]
    provider_used: str


@app.post("/tts", response_model=TTSResponse,
          dependencies=[Depends(require_api_key)])
async def tts(req: TTSRequest):
    job_id = uuid.uuid4().hex[:12]
    out = WORKSPACE / req.object_prefix / f"{job_id}.mp3"
    try:
        path, bounds, prov = await synthesize(
            req.text, out, voice=req.voice, provider=req.provider  # type: ignore
        )
    except Exception as e:
        log.exception("tts_failed", error=str(e))
        raise HTTPException(500, f"TTS failed: {e}")

    duration = get_duration_sec(path)
    storage_url = None
    if req.upload:
        storage_url = get_storage().upload_file(
            path, f"{req.object_prefix}/{job_id}.mp3"
        )
    return TTSResponse(
        storage_url=storage_url,
        local_path=str(path),
        duration_sec=duration,
        word_boundaries=bounds,
        provider_used=prov,
    )


@app.get("/tts/voices", dependencies=[Depends(require_api_key)])
async def tts_voices(language: Optional[str] = None):
    return await list_voices(language=language)


# ---------- Stock image search ----------
class ImageSearchRequest(BaseModel):
    keywords: list[str]
    download: bool = True
    upload: bool = True
    object_prefix: str = "images"


@app.post("/images/search", dependencies=[Depends(require_api_key)])
async def images_search(req: ImageSearchRequest):
    image = await find_best_image(req.keywords)
    if not image:
        raise HTTPException(404, "No image found for given keywords")

    result = {"meta": image, "storage_url": None, "local_path": None}
    if req.download:
        job_id = uuid.uuid4().hex[:12]
        local = WORKSPACE / req.object_prefix / f"{job_id}.jpg"
        await download_image(image, local)
        result["local_path"] = str(local)
        if req.upload:
            result["storage_url"] = get_storage().upload_file(
                local, f"{req.object_prefix}/{job_id}.jpg"
            )
    return result


# ---------- Render full short ----------
class SegmentInput(BaseModel):
    text: str
    image_url: str          # URL HTTP descargable (puede ser MinIO publico)
    audio_url: str          # URL HTTP descargable
    duration_sec: float


class RenderRequest(BaseModel):
    short_id: str
    segments: list[SegmentInput]
    music_url: Optional[str] = None
    burn_subtitles: bool = True
    upload: bool = True


class RenderResponse(BaseModel):
    storage_url: Optional[str]
    local_path: str
    duration_sec: float
    file_size_bytes: int


async def _download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        r = await client.get(url)
        r.raise_for_status()
        dest.write_bytes(r.content)
    return dest


@app.post("/render", response_model=RenderResponse,
          dependencies=[Depends(require_api_key)])
async def render(req: RenderRequest):
    work = WORKSPACE / "render" / req.short_id
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)

    segments: list[Segment] = []
    for i, s in enumerate(req.segments):
        img_local = work / f"in_img_{i:02d}.jpg"
        aud_local = work / f"in_aud_{i:02d}.mp3"
        await _download(s.image_url, img_local)
        await _download(s.audio_url, aud_local)
        segments.append(Segment(
            image_path=img_local,
            audio_path=aud_local,
            text=s.text,
            duration_sec=s.duration_sec,
        ))

    music_local: Optional[Path] = None
    if req.music_url:
        music_local = work / "music.mp3"
        await _download(req.music_url, music_local)

    output = work / "final.mp4"
    try:
        render_short(
            segments, work, output,
            music_path=music_local, burn_subtitles=req.burn_subtitles,
        )
    except Exception as e:
        log.exception("render_failed", short_id=req.short_id, error=str(e))
        raise HTTPException(500, f"Render failed: {e}")

    duration = get_duration_sec(output)
    size = output.stat().st_size
    storage_url = None
    if req.upload:
        storage_url = get_storage().upload_file(
            output, f"shorts/{req.short_id}.mp4"
        )
    return RenderResponse(
        storage_url=storage_url,
        local_path=str(output),
        duration_sec=duration,
        file_size_bytes=size,
    )


# ---------- Script generation (GPT) ----------
class ScriptRequest(BaseModel):
    candidate_id: int
    language: str = "es"
    force_regenerate: bool = False


@app.post("/script", dependencies=[Depends(require_api_key)])
async def script_endpoint(req: ScriptRequest):
    """Genera (o devuelve cacheado) el guion JSON estructurado para un candidato."""
    candidate = await db.get_candidate(req.candidate_id)
    if not candidate:
        raise HTTPException(404, f"Candidate {req.candidate_id} not found")

    if not req.force_regenerate:
        existing = await db.get_script(req.candidate_id, req.language)
        if existing:
            return {
                "script_id": existing["id"],
                "candidate_id": req.candidate_id,
                "language": req.language,
                "title": existing["title"],
                "seo_description": existing["seo_description"],
                "tags": existing["tags"],
                "hook": existing["hook"],
                "segments": existing["segments"],
                "total_estimated_sec": float(existing["total_estimated_sec"] or 0),
                "llm_cost_usd": float(existing["llm_cost_usd"] or 0),
                "cached": True,
            }

    try:
        parsed = await generate_script(candidate, language=req.language)
    except Exception as e:
        log.exception("script_gen_failed", error=str(e))
        raise HTTPException(500, f"Script generation failed: {e}")

    meta = parsed.pop("_meta", {})
    script_id = await db.insert_script(
        candidate_id=req.candidate_id, language=req.language,
        title=parsed["title"], seo_description=parsed["seo_description"],
        tags=parsed["tags"], hook=parsed["hook"],
        segments=parsed["segments"],
        total_estimated_sec=parsed.get("total_estimated_sec", 0),
        llm_model=meta.get("model", ""),
        tokens_in=meta.get("tokens_in", 0),
        tokens_out=meta.get("tokens_out", 0),
        cost_usd=meta.get("cost_usd", 0),
    )
    await db.log_cost(
        service="openai", operation="script_generation",
        units=meta.get("tokens_in", 0) + meta.get("tokens_out", 0),
        cost_usd=meta.get("cost_usd", 0),
        script_id=script_id,
        meta={"model": meta.get("model"), "candidate_id": req.candidate_id},
    )
    return {
        "script_id": script_id,
        "candidate_id": req.candidate_id,
        "language": req.language,
        "title": parsed["title"],
        "seo_description": parsed["seo_description"],
        "tags": parsed["tags"],
        "hook": parsed["hook"],
        "segments": parsed["segments"],
        "total_estimated_sec": parsed.get("total_estimated_sec", 0),
        "llm_cost_usd": meta.get("cost_usd", 0),
        "cached": False,
    }


# ---------- Produce full short ----------
class ProduceRequest(BaseModel):
    candidate_id: int
    language: str = "es"
    voice: Optional[str] = None
    force_regenerate_script: bool = False
    burn_subtitles: bool = True
    music_url: Optional[str] = None


@app.post("/produce", dependencies=[Depends(require_api_key)])
async def produce_endpoint(req: ProduceRequest):
    """Pipeline completo: candidato -> guion -> assets -> MP4 final.

    Tarda ~1-3 min por short en VPS basico. Asegurate de que el cliente
    (n8n) tenga timeout >= 300000 ms.
    """
    # Limite diario configurable (policy_params)
    max_per_day = await db.policy_get("max_shorts_per_day", default=5)
    try:
        max_n = int(max_per_day) if not isinstance(max_per_day, int) else max_per_day
    except (TypeError, ValueError):
        max_n = 5
    today = await db.shorts_published_today(language=req.language)
    if today >= max_n:
        raise HTTPException(
            429,
            f"Daily limit reached ({today}/{max_n}) for language={req.language}",
        )

    try:
        result = await produce_short(
            candidate_id=req.candidate_id,
            language=req.language,
            voice=req.voice,
            force_regenerate_script=req.force_regenerate_script,
            burn_subtitles=req.burn_subtitles,
            music_url=req.music_url,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        log.exception("produce_failed", candidate_id=req.candidate_id, error=str(e))
        raise HTTPException(500, f"Production failed: {e}")

    return result


# ---------- Inspeccion / utilidades ----------
@app.get("/produce/queue", dependencies=[Depends(require_api_key)])
async def produce_queue(limit: int = 10):
    """Devuelve los siguientes candidatos en estado 'queued'."""
    return await db.pick_next_queued(limit=limit)


@app.get("/produce/today", dependencies=[Depends(require_api_key)])
async def produce_today(language: Optional[str] = None):
    """Cuantos shorts se han producido hoy (por idioma o total)."""
    n = await db.shorts_published_today(language=language)
    max_n = await db.policy_get("max_shorts_per_day", default=5)
    try:
        max_n = int(max_n)
    except (TypeError, ValueError):
        max_n = 5
    return {"language": language, "produced_today": n, "max_per_day": max_n}


# ---------- AI Story generation ----------
class StoryGenRequest(BaseModel):
    count: int = 1
    language: str = "es"
    themes: Optional[list[str]] = None
    settings_pool: Optional[list[str]] = None
    tones: Optional[list[str]] = None
    min_words: int = 1500
    max_words: int = 2800
    auto_queue: bool = True   # si True -> status='queued', si False -> 'new'


@app.post("/stories/generate", dependencies=[Depends(require_api_key)])
async def stories_generate(req: StoryGenRequest):
    """Genera N historias originales con GPT y las inserta en story_candidates."""
    if req.count < 1 or req.count > 10:
        raise HTTPException(400, "count must be 1..10")

    # Lee bancos desde policy_params si el request no los provee
    themes = req.themes
    if not themes:
        pol = await db.policy_get("story_themes")
        themes = pol if isinstance(pol, list) and pol else DEFAULT_THEMES
    settings_pool = req.settings_pool
    if not settings_pool:
        pol = await db.policy_get("story_settings")
        settings_pool = pol if isinstance(pol, list) and pol else DEFAULT_SETTINGS
    tones = req.tones
    if not tones:
        pol = await db.policy_get("story_tones")
        tones = pol if isinstance(pol, list) and pol else DEFAULT_TONES

    created = []
    errors = []
    status = "queued" if req.auto_queue else "new"

    for i in range(req.count):
        try:
            story = await generate_story(
                language=req.language,
                themes_pool=themes,
                settings_pool=settings_pool,
                tones_pool=tones,
                min_words=req.min_words,
                max_words=req.max_words,
            )
            meta = story["_meta"]
            cid = await db.insert_ai_candidate(
                title=story["title"],
                selftext=story["story"],
                selftext_hash=meta["selftext_hash"],
                language=meta["language"],
                theme=meta["theme"],
                setting=meta["setting"],
                tone=meta["tone"],
                word_count=meta["word_count"],
                quality_score=story["self_quality_score"],
                horror_score=story["self_horror_score"],
                self_assessment=story.get("self_assessment"),
                status=status,
                gen_model=meta["model"],
                gen_cost_usd=meta["cost_usd"],
                external_id=meta["selftext_hash"],  # idempotencia por hash
            )
            await db.log_cost(
                service="openai", operation="story_generation",
                units=meta["tokens_in"] + meta["tokens_out"],
                cost_usd=meta["cost_usd"],
                meta={"candidate_id": cid, "model": meta["model"],
                      "theme": meta["theme"], "setting": meta["setting"]},
            )
            created.append({
                "candidate_id": cid,
                "title": story["title"],
                "theme": meta["theme"],
                "setting": meta["setting"],
                "tone": meta["tone"],
                "word_count": meta["word_count"],
                "self_quality": story["self_quality_score"],
                "self_horror": story["self_horror_score"],
                "cost_usd": meta["cost_usd"],
            })
        except Exception as e:
            log.exception("story_gen_failed", index=i, error=str(e))
            errors.append({"index": i, "error": str(e)})

    return {
        "requested": req.count,
        "created": len(created),
        "candidates": created,
        "errors": errors,
        "total_cost_usd": round(sum(c["cost_usd"] for c in created), 5),
    }


@app.get("/stories/today", dependencies=[Depends(require_api_key)])
async def stories_today(language: Optional[str] = None):
    """Cuantas historias IA se han generado hoy."""
    n = await db.stories_generated_today(language=language)
    target = await db.policy_get("stories_per_day", default=5)
    try:
        target = int(target)
    except (TypeError, ValueError):
        target = 5
    return {"language": language, "generated_today": n, "target_per_day": target}




# ---------- YouTube publishing (WF4) ----------
class PublishRequest(BaseModel):
    short_id: int
    privacy: Optional[str] = None        # public | unlisted | private
    category_id: Optional[str] = None


@app.post("/publish", dependencies=[Depends(require_api_key)])
async def publish_endpoint(req: PublishRequest):
    """Sube un Short rendered a YouTube via Data API v3.

    Tarda 30-90s segun tamano. Usa cuota YouTube: 1 upload = 1.600 units
    sobre 10.000/dia gratis = ~6 uploads/dia por proyecto GCP por defecto.
    """
    short = await db.get_short(req.short_id)
    if not short:
        raise HTTPException(404, f"Short {req.short_id} not found")

    if short["status"] == "published" and short.get("youtube_video_id"):
        return {
            "short_id": req.short_id,
            "youtube_video_id": short["youtube_video_id"],
            "youtube_url": f"https://youtube.com/shorts/{short['youtube_video_id']}",
            "status": "already_published",
        }

    if short["status"] not in ("rendered", "uploading"):
        raise HTTPException(
            409,
            f"Short {req.short_id} status={short['status']} not uploadable",
        )

    # Lazy import para no requerir libs Google al arrancar si no se usa
    try:
        from .youtube import upload_video
    except ImportError as e:
        raise HTTPException(500, f"YouTube libs not installed: {e}")

    # Marcar uploading (con error_message=NULL para limpiar reintentos)
    await db.update_short_status(req.short_id, "uploading")

    # Descargar el MP4 desde MinIO a workspace local
    work = WORKSPACE / "publish" / str(req.short_id)
    work.mkdir(parents=True, exist_ok=True)
    local_mp4 = work / "final.mp4"

    try:
        # final_video_url apunta a minio:9000/... -> reemplazamos por endpoint interno
        url = short["final_video_url"]
        if "minio:9000" not in url and "/horror-assets/" in url:
            # url ya esta en formato relativo aceptable
            pass
        # Descargar con httpx (la URL apunta a minio:9000 desde la red Docker)
        await _download(url, local_mp4)

        result = await upload_video(
            local_mp4,
            title=short["title"] or "",
            description=short["description"] or "",
            tags=short["tags"] or [],
            privacy=req.privacy,
            category_id=req.category_id,
        )

        yt_id = result.get("id")
        if not yt_id:
            raise RuntimeError(f"YouTube no devolvio id: {result}")

        # status -> published
        await db.set_short_youtube(
            req.short_id,
            youtube_video_id=yt_id,
            channel_id=result.get("snippet", {}).get("channelId"),
        )
        await db.log_cost(
            service="youtube", operation="video_upload",
            units=1600, cost_usd=0,   # gratis dentro de quota diaria
            short_id=req.short_id,
            meta={"video_id": yt_id, "privacy": req.privacy or "public"},
        )

        return {
            "short_id": req.short_id,
            "youtube_video_id": yt_id,
            "youtube_url": f"https://youtube.com/shorts/{yt_id}",
            "status": "published",
            "duration_sec": float(short["duration_sec"] or 0),
            "title": short["title"],
        }

    except Exception as e:
        log.exception("publish_failed", short_id=req.short_id, error=str(e))
        # revertir status a rendered (no perder el MP4)
        await db.update_short_status(req.short_id, "rendered", error=str(e))
        raise HTTPException(500, f"Publish failed: {e}")


@app.get("/publish/queue", dependencies=[Depends(require_api_key)])
async def publish_queue(limit: int = 10, language: Optional[str] = None):
    """Lista shorts con status='rendered' listos para subir."""
    return await db.pick_next_rendered(limit=limit, language=language)


@app.get("/publish/today", dependencies=[Depends(require_api_key)])
async def publish_today(language: Optional[str] = None):
    """Cuantos shorts publicados hoy y limite del policy."""
    n = await db.shorts_uploaded_today(language=language)
    max_n = await db.policy_get("max_uploads_per_day", default=5)
    try:
        max_n = int(max_n)
    except (TypeError, ValueError):
        max_n = 5
    return {"language": language, "uploaded_today": n, "max_per_day": max_n}


# ---------- TikTok publishing (WF5) ----------
class PublishTikTokRequest(BaseModel):
    short_id: int
    mode: Optional[str] = None              # 'inbox' | 'direct'
    privacy_level: Optional[str] = None     # solo aplica en 'direct'
    poll: bool = True


@app.post("/publish-tiktok", dependencies=[Depends(require_api_key)])
async def publish_tiktok_endpoint(req: PublishTikTokRequest):
    """Sube un Short rendered a TikTok via Content Posting API.

    Modos:
    - 'inbox' (default): video llega al inbox/drafts del creador. NO requiere
      app audit. El creador debe darle "Post" en la app de TikTok para
      publicarlo. Util para trabajar antes de pasar audit.
    - 'direct':   publica directamente. Requiere app audit (1-2 sem) +
      scope video.publish.

    El modo se controla globalmente con policy_params.tiktok_publish_mode.
    """
    short = await db.get_short(req.short_id)
    if not short:
        raise HTTPException(404, f"Short {req.short_id} not found")

    # Validacion del estado del short
    if short["status"] not in ("rendered", "uploading", "published"):
        raise HTTPException(
            409,
            f"Short {req.short_id} status={short['status']} not eligible for TikTok",
        )

    # Idempotencia: si ya publicado en TikTok, devolver
    if short.get("tiktok_status") == "published" and short.get("tiktok_publish_id"):
        return {
            "short_id": req.short_id,
            "tiktok_publish_id": short["tiktok_publish_id"],
            "tiktok_video_id": short.get("tiktok_video_id"),
            "tiktok_url": short.get("tiktok_url"),
            "status": "already_published",
        }

    # Toggle global
    enabled = await db.policy_get("tiktok_enabled", default=True)
    if not enabled:
        raise HTTPException(503, "TikTok publishing is disabled in policy_params")

    # Lazy import para no romper si las libs no estan
    try:
        from .tiktok import upload_video as tiktok_upload
    except ImportError as e:
        raise HTTPException(500, f"TikTok module not available: {e}")

    # Resolver modo y privacy
    mode = req.mode or await db.policy_get("tiktok_publish_mode", default="inbox")
    if not isinstance(mode, str):
        mode = "inbox"
    privacy = req.privacy_level or await db.policy_get(
        "tiktok_privacy_level", default="SELF_ONLY",
    )

    # Marcar uploading
    await db.set_short_tiktok(req.short_id, status="uploading")

    # Descargar el MP4 del MinIO al workspace local
    work = WORKSPACE / "publish-tiktok" / str(req.short_id)
    work.mkdir(parents=True, exist_ok=True)
    local_mp4 = work / "final.mp4"

    try:
        await _download(short["final_video_url"], local_mp4)

        result = await tiktok_upload(
            local_mp4,
            title=short["title"] or "",
            description=short["description"] or "",
            tags=short["tags"] or [],
            mode=mode,
            privacy_level=privacy if isinstance(privacy, str) else None,
            poll=req.poll,
        )

        publish_id = result.get("publish_id")
        tiktok_status_raw = (result.get("status") or "").upper()

        # Mapear status de TikTok a nuestro enum interno
        if tiktok_status_raw in ("PUBLISH_COMPLETE", "PUBLISH_OK"):
            our_status = "published"
        elif tiktok_status_raw == "SEND_TO_USER_INBOX":
            # Modo inbox: el video llego al inbox del creador. Lo marcamos
            # published a efectos del pipeline (la entrega fue exitosa).
            our_status = "published"
        elif tiktok_status_raw in ("FAILED", "PUBLISH_FAILED"):
            our_status = "failed"
        elif tiktok_status_raw in ("PROCESSING_DOWNLOAD", "PROCESSING_UPLOAD"):
            # TikTok aun lo procesa. Lo marcamos published (best-effort) y el
            # proximo poll lo refinara si fuera necesario.
            our_status = "published"
        else:
            our_status = "uploading"

        url = (
            f"https://www.tiktok.com/@me/video/{publish_id}"
            if our_status == "published" else None
        )

        await db.set_short_tiktok(
            req.short_id,
            publish_id=publish_id,
            status=our_status,
            url=url,
        )

        await db.log_cost(
            service="tiktok", operation="video_upload",
            units=1, cost_usd=0,
            short_id=req.short_id,
            meta={"publish_id": publish_id, "mode": mode,
                  "tiktok_status": tiktok_status_raw},
        )

        return {
            "short_id": req.short_id,
            "tiktok_publish_id": publish_id,
            "tiktok_status": tiktok_status_raw,
            "status": our_status,
            "mode": mode,
            "url": url,
            "title": short["title"],
        }

    except Exception as e:
        log.exception("publish_tiktok_failed", short_id=req.short_id, error=str(e))
        await db.set_short_tiktok(req.short_id, status="failed", error=str(e))
        raise HTTPException(500, f"TikTok publish failed: {e}")


@app.get("/publish-tiktok/queue", dependencies=[Depends(require_api_key)])
async def publish_tiktok_queue(limit: int = 10, language: Optional[str] = None):
    """Lista shorts pendientes de subir a TikTok."""
    return await db.pick_next_for_tiktok(limit=limit, language=language)


@app.get("/publish-tiktok/today", dependencies=[Depends(require_api_key)])
async def publish_tiktok_today(language: Optional[str] = None):
    """Cuantos shorts subidos hoy a TikTok y limite de policy."""
    n = await db.tiktok_uploaded_today(language=language)
    max_n = await db.policy_get("max_tiktok_uploads_per_day", default=10)
    try:
        max_n = int(max_n)
    except (TypeError, ValueError):
        max_n = 10
    return {"language": language, "uploaded_today": n, "max_per_day": max_n}
