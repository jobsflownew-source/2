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

from .config import settings
from .images import download_image, find_best_image
from .security import require_api_key
from .storage import get_storage
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
