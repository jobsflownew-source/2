"""Pipeline de produccion: candidato -> script -> assets -> MP4 final.

Punto de entrada: produce_short(candidate_id, language, ...).

Es sincrono dentro de la peticion HTTP (~1-3 minutos por short en VPS basico).
Si en el futuro quieres asincrono, mete una cola Redis y devuelve job_id.
"""
from __future__ import annotations

import asyncio
import logging
import random
import shutil
from pathlib import Path
from typing import Optional

from PIL import Image

from . import db
from .config import settings
from .images import download_image, find_best_image, find_image_candidates
from .images_placeholder import generate_placeholder_image
from .image_selection import select_best_image_with_ai
from .script_gen import generate_script
from .storage import get_storage
from .tts import synthesize
from .video import Segment, get_duration_sec, render_short

log = logging.getLogger(__name__)


WORKSPACE = Path(settings.workspace_dir)


async def _ensure_script(candidate: dict, language: str,
                         force_regenerate: bool = False) -> tuple[int, dict]:
    """Devuelve (script_id, script_data). Reutiliza si existe."""
    if not force_regenerate:
        existing = await db.get_script(candidate["id"], language)
        if existing:
            log.info("script_reused candidate=%s id=%s",
                     candidate["id"], existing["id"])
            return existing["id"], {
                "title": existing["title"],
                "seo_description": existing["seo_description"],
                "tags": existing["tags"],
                "hook": existing["hook"],
                "segments": existing["segments"],
                "total_estimated_sec": float(existing["total_estimated_sec"] or 0),
            }

    parsed = await generate_script(candidate, language=language)
    meta = parsed.pop("_meta", {})

    script_id = await db.insert_script(
        candidate_id=candidate["id"],
        language=language,
        title=parsed["title"],
        seo_description=parsed["seo_description"],
        tags=parsed["tags"],
        hook=parsed["hook"],
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
        meta={"model": meta.get("model"), "candidate_id": candidate["id"]},
    )
    return script_id, parsed


async def _produce_segment_audio(
    segment: dict, voice: str, work_dir: Path,
    script_id: int, segment_index: int,
) -> tuple[Path, float]:
    audio_path = work_dir / f"audio_{segment_index:02d}.mp3"
    text_for_tts = (
        (segment.get("text") or "")
        if segment_index > 0
        else f"{segment.get('text','')}"
    )
    _, _, provider_used = await synthesize(
        text_for_tts, audio_path, voice=voice, provider="auto"
    )
    duration = get_duration_sec(audio_path)
    storage = get_storage()
    obj = f"audio/{script_id}/seg_{segment_index:02d}.mp3"
    url = storage.upload_file(audio_path, obj)
    await db.insert_asset(
        script_id=script_id, segment_index=segment_index,
        asset_type="audio", source=provider_used,
        storage_url=url,
        duration_ms=int(duration * 1000),
        file_size_bytes=audio_path.stat().st_size,
        meta={"voice": voice},
    )
    return audio_path, duration


async def _produce_segment_image(
    segment: dict, work_dir: Path,
    script_id: int, segment_index: int,
    excluded_urls: Optional[set[str]] = None,
    ai_selection: bool = True,
    ai_candidates_count: int = 6,
    ai_model: str = "gpt-4o-mini",
) -> Path:
    """Obtiene una imagen para el segmento.

    Cascada:
    1. Si ai_selection=True: pide find_image_candidates (top_k) y deja
       que GPT-4o-mini-vision elija la mejor para el mood/keywords/texto.
       Si la IA falla, cae a (2).
    2. find_best_image con las keywords del segmento (random pick entre
       las disponibles, excluyendo las ya usadas en N dias).
    3. find_best_image con keyword generica segun el mood.
    4. generate_placeholder_image local -> JAMAS falla.

    Despues de elegir una imagen externa, se registra en used_images
    para que no vuelva a aparecer en el cooldown configurado.
    """
    keywords = segment.get("keywords") or []
    mood = segment.get("mood") or "tension"
    excluded = excluded_urls if excluded_urls is not None else set()

    image_meta: Optional[dict] = None
    ai_selection_meta: Optional[dict] = None

    # ---- 1. AI-driven selection ----
    if ai_selection and keywords:
        candidates = await find_image_candidates(
            keywords, excluded_urls=excluded, top_k=ai_candidates_count,
        )
        if not candidates:
            # Probar con fallback keywords ANTES de soltar el AI
            fallback_kw = _fallback_keywords_for_mood(mood)
            candidates = await find_image_candidates(
                fallback_kw, excluded_urls=excluded, top_k=ai_candidates_count,
            )
        if candidates:
            picked = await select_best_image_with_ai(
                segment_text=segment.get("text", ""),
                mood=mood,
                keywords=keywords,
                candidates=candidates,
                model=ai_model,
            )
            if picked:
                image_meta = picked
                ai_selection_meta = picked.get("ai_selection")
            else:
                # IA fallo: pick aleatorio entre las que ya tenemos descargadas
                image_meta = random.choice(candidates) if candidates else None

    # ---- 2 + 3. fallback al random pick (sin IA) ----
    if not image_meta:
        image_meta = await find_best_image(keywords, excluded_urls=excluded)
    if not image_meta:
        fallback_kw = _fallback_keywords_for_mood(mood)
        image_meta = await find_best_image(fallback_kw, excluded_urls=excluded)

    img_path = work_dir / f"img_{segment_index:02d}.jpg"

    if image_meta:
        await download_image(image_meta, img_path)
        source = image_meta["source"]
        original_url = image_meta.get("download") or image_meta.get("url") or ""
        # Registrar para que el dedup futuro la excluya. Tambien la
        # anadimos al set local para que el mismo short no use la misma
        # imagen en varios segmentos.
        if original_url:
            await db.record_image_used(
                image_url=original_url, source=source,
                script_id=script_id, segment_index=segment_index,
            )
            excluded.add(original_url)
        meta_extra = {
            "author": image_meta.get("author"),
            "keywords": keywords[:5],
            "image_url": original_url,
        }
        if ai_selection_meta:
            meta_extra["ai_selection"] = ai_selection_meta
            # Loguear coste del scoring para tracking
            cost = ai_selection_meta.get("cost_usd", 0.0) or 0.0
            if cost > 0:
                await db.log_cost(
                    service="openai", operation="image_selection_vision",
                    units=ai_selection_meta.get("tokens_in", 0)
                          + ai_selection_meta.get("tokens_out", 0),
                    cost_usd=cost,
                    script_id=script_id,
                    meta={
                        "model": ai_selection_meta.get("model"),
                        "candidates_count": ai_selection_meta.get("candidates_count"),
                        "score": ai_selection_meta.get("score"),
                        "segment_index": segment_index,
                    },
                )
    else:
        # Sin APIs configuradas o todas fallaron -> placeholder local
        log.warning(
            "no_stock_image_found segment=%s mood=%s using_placeholder",
            segment_index, mood,
        )
        seed = f"{script_id}_{segment_index}_{'-'.join(keywords[:3])}"
        await asyncio.to_thread(generate_placeholder_image, img_path, mood, seed)
        source = "placeholder"
        meta_extra = {"keywords": keywords[:5], "mood": mood}

    with Image.open(img_path) as im:
        w, h = im.size

    storage = get_storage()
    obj = f"images/{script_id}/seg_{segment_index:02d}.jpg"
    url = storage.upload_file(img_path, obj)
    await db.insert_asset(
        script_id=script_id, segment_index=segment_index,
        asset_type="image", source=source,
        storage_url=url, width=w, height=h,
        file_size_bytes=img_path.stat().st_size,
        meta=meta_extra,
    )
    return img_path


def _fallback_keywords_for_mood(mood: str) -> list[str]:
    """Keywords genericas por mood, en ingles, para el fallback de busqueda."""
    return {
        "tension": ["dark forest fog"],
        "fear": ["abandoned hallway dim"],
        "despair": ["empty room shadow"],
        "reveal": ["open door darkness"],
        "aftermath": ["broken window night"],
    }.get(mood, ["abandoned house at night"])


async def produce_short(
    candidate_id: int,
    language: str = "es",
    voice: Optional[str] = None,
    force_regenerate_script: bool = False,
    burn_subtitles: bool = True,
    music_url: Optional[str] = None,
) -> dict:
    """Orquesta la produccion completa de un short."""
    candidate = await db.get_candidate(candidate_id)
    if not candidate:
        raise ValueError(f"Candidate {candidate_id} no existe")

    # 1. Guion
    script_id, script = await _ensure_script(
        candidate, language, force_regenerate=force_regenerate_script,
    )
    log.info("produce script_id=%s segs=%d", script_id, len(script["segments"]))

    # Selector de voz: si no se pasa explicita, rota desde el banco curado
    if not voice:
        voice = await db.pick_next_voice(language=language)
        await db.record_voice_use(voice)
        log.info("voice_auto_selected voice=%s script_id=%s", voice, script_id)
    work_dir = WORKSPACE / "produce" / f"script_{script_id}"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    # 2. Assets por segmento (paralelo)
    # Cargar el set de URLs ya usadas (cooldown configurable en
    # policy_params.image_cooldown_days, default 30 dias) UNA sola vez
    # para todo el short, para que las imagenes elegidas en este short
    # tampoco se repitan entre segmentos.
    excluded_urls = await db.get_recent_image_urls()

    # Leer config de seleccion con IA (policy_params, evaluado por short
    # para que los cambios apliquen sin redeploy).
    ai_selection_raw = await db.policy_get("image_ai_selection", default=True)
    ai_selection_enabled = bool(ai_selection_raw) and bool(settings.openai_api_key)
    ai_candidates_count_raw = await db.policy_get(
        "image_ai_candidates_count", default=6,
    )
    try:
        ai_candidates_count = int(ai_candidates_count_raw)
    except (TypeError, ValueError):
        ai_candidates_count = 6
    ai_model = await db.policy_get("image_ai_model", default="gpt-4o-mini")
    if not isinstance(ai_model, str) or not ai_model:
        ai_model = "gpt-4o-mini"

    log.info(
        "image_pipeline_config script_id=%s excluded=%d ai_selection=%s "
        "candidates=%d model=%s",
        script_id, len(excluded_urls), ai_selection_enabled,
        ai_candidates_count, ai_model,
    )

    segments = script["segments"]
    tasks = []
    for i, seg in enumerate(segments):
        tasks.append(_produce_segment_audio(seg, voice, work_dir, script_id, i))
        tasks.append(_produce_segment_image(
            seg, work_dir, script_id, i,
            excluded_urls=excluded_urls,
            ai_selection=ai_selection_enabled,
            ai_candidates_count=ai_candidates_count,
            ai_model=ai_model,
        ))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    # Comprobar errores
    for r in results:
        if isinstance(r, Exception):
            raise r

    audio_results = results[0::2]   # [(path, dur), ...]
    image_results = results[1::2]   # [path, ...]

    # 3. Construir Segments para render
    render_segments: list[Segment] = []
    actual_total = 0.0
    for i, seg in enumerate(segments):
        audio_path, dur = audio_results[i]
        img_path = image_results[i]
        actual_total += dur
        render_segments.append(Segment(
            image_path=img_path,
            audio_path=audio_path,
            text=seg["text"],
            duration_sec=dur,
        ))

    # 4. Render final
    output_path = work_dir / "final.mp4"
    music_path: Optional[Path] = None
    if music_url:
        # download to local
        import httpx
        music_path = work_dir / "music.mp3"
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
            r = await c.get(music_url)
            r.raise_for_status()
            music_path.write_bytes(r.content)

    await asyncio.to_thread(
        render_short, render_segments, work_dir, output_path,
        music_path, burn_subtitles,
    )

    final_duration = get_duration_sec(output_path)
    file_size = output_path.stat().st_size

    # 5. Subir final a MinIO
    storage = get_storage()
    final_obj = f"shorts/{script_id}/final.mp4"
    final_url = storage.upload_file(output_path, final_obj)

    await db.insert_asset(
        script_id=script_id, segment_index=None,
        asset_type="video", source="ffmpeg",
        storage_url=final_url,
        duration_ms=int(final_duration * 1000),
        width=1080, height=1920,
        file_size_bytes=file_size,
    )

    # 6. Crear short row
    short_id = await db.insert_short(
        script_id=script_id,
        language=language,
        title=script["title"],
        description=script["seo_description"],
        tags=script["tags"],
        voice_id=voice,
        image_style="stock_mixed",
        final_video_url=final_url,
        duration_sec=final_duration,
        file_size_bytes=file_size,
        status="rendered",
    )

    # 7. Marcar candidato
    await db.mark_candidate_status(candidate_id, "produced")

    return {
        "short_id": short_id,
        "script_id": script_id,
        "candidate_id": candidate_id,
        "language": language,
        "title": script["title"],
        "description": script["seo_description"],
        "tags": script["tags"],
        "voice": voice,
        "duration_sec": final_duration,
        "file_size_bytes": file_size,
        "video_url": final_url,
        "estimated_total_sec": script.get("total_estimated_sec"),
        "actual_total_sec": round(actual_total, 2),
        "segments_count": len(segments),
    }
