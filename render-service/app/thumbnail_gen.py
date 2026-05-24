"""Generador de thumbnails con DALL-E 3.

Dado un script (title, hook, segments), construye un prompt
cinematografico con el mood del primer segmento y pide a DALL-E
una imagen 1024x1792 vertical. La descarga, la procesa a 1080x1920
con PIL y la guarda en disco para que el pipeline la suba a MinIO.

Coste: ~$0.040 por thumbnail con dall-e-3 standard 1024x1792.
Si la API falla o OPENAI_API_KEY no esta configurada, devuelve None
y el pipeline sigue sin thumbnail (YouTube usara primer frame).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import httpx
from PIL import Image
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import settings


log = logging.getLogger(__name__)


DALLE_URL = "https://api.openai.com/v1/images/generations"

# Coste por imagen en USD (DALL-E 3 a marzo 2026, standard quality).
COST_PER_IMAGE = {
    ("dall-e-3", "standard", "1024x1024"): 0.040,
    ("dall-e-3", "standard", "1024x1792"): 0.040,
    ("dall-e-3", "standard", "1792x1024"): 0.040,
    ("dall-e-3", "hd", "1024x1024"): 0.080,
    ("dall-e-3", "hd", "1024x1792"): 0.120,
    ("dall-e-3", "hd", "1792x1024"): 0.120,
    ("dall-e-2", "standard", "1024x1024"): 0.020,
    ("dall-e-2", "standard", "512x512"): 0.018,
}

# Output dimensions (Shorts spec)
OUTPUT_W = 1080
OUTPUT_H = 1920


def _build_prompt(title: str, hook: str, mood: str,
                  first_segment_kw: list[str],
                  style_suffix: str) -> str:
    """Construye el prompt para DALL-E.

    Estrategia: combinar titulo + hook (1 frase) + mood + 3 keywords
    del primer segmento + style suffix. DALL-E 3 acepta hasta ~4000
    chars en el prompt; nosotros nos quedamos por debajo de 800.
    """
    # Limpia caracteres que confunden a DALL-E (comillas dramaticas)
    title_c = title.replace('"', "").replace("'", "").strip()
    hook_c = hook.replace('"', "").replace("'", "").strip() if hook else ""

    mood_descriptor = {
        "tension":   "rising tension, suspense building, narrow corridor",
        "fear":      "raw fear, sudden dread, person frozen in place",
        "despair":   "hopelessness, abandoned space, decay",
        "reveal":    "shocking discovery, hidden truth surfacing",
        "aftermath": "destruction left behind, silence after horror",
        "default":   "ominous atmosphere",
    }.get((mood or "default").lower(), "ominous atmosphere")

    keywords_part = ", ".join(first_segment_kw[:3]) if first_segment_kw else ""

    parts = [
        f"Movie poster style horror scene: {title_c}",
    ]
    if hook_c and len(hook_c) < 200:
        parts.append(f"Concept: {hook_c}")
    parts.append(f"Mood: {mood_descriptor}")
    if keywords_part:
        parts.append(f"Visual elements: {keywords_part}")
    parts.append(style_suffix)

    prompt = ". ".join(parts)
    # Cap defensivo
    return prompt[:1000]


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1.5, min=2, max=10),
    reraise=True,
)
async def _call_dalle(prompt: str, model: str, quality: str,
                      size: str) -> dict:
    """Llama al endpoint /images/generations y devuelve el JSON crudo."""
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY no configurada")

    payload: dict = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": size,
        "response_format": "url",
    }
    if model == "dall-e-3":
        payload["quality"] = quality
        # 'natural' es menos sensacionalista que 'vivid', mejor para horror sutil
        payload["style"] = "natural"

    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(DALLE_URL, json=payload, headers=headers)
        if r.status_code >= 400:
            log.error("dalle_http_error status=%s body=%s",
                      r.status_code, r.text[:500])
        r.raise_for_status()
        return r.json()


def _resize_to_short(src: Path, dst: Path) -> None:
    """Resize/crop la imagen DALL-E (1024x1792) a 1080x1920.

    Usa LANCZOS para calidad y guarda como JPEG quality 92.
    Si el aspect ya es muy parecido (1024/1792 ≈ 1080/1920) hacemos
    un resize directo. Si no, crop centrado.
    """
    with Image.open(src) as img:
        img = img.convert("RGB")
        target_ratio = OUTPUT_W / OUTPUT_H
        src_ratio = img.width / img.height

        if abs(src_ratio - target_ratio) < 0.02:
            out = img.resize((OUTPUT_W, OUTPUT_H), Image.LANCZOS)
        elif src_ratio > target_ratio:
            # mas ancho de lo necesario -> crop horizontal centrado
            new_w = int(img.height * target_ratio)
            left = (img.width - new_w) // 2
            cropped = img.crop((left, 0, left + new_w, img.height))
            out = cropped.resize((OUTPUT_W, OUTPUT_H), Image.LANCZOS)
        else:
            # mas alto de lo necesario -> crop vertical centrado
            new_h = int(img.width / target_ratio)
            top = (img.height - new_h) // 2
            cropped = img.crop((0, top, img.width, top + new_h))
            out = cropped.resize((OUTPUT_W, OUTPUT_H), Image.LANCZOS)
        out.save(dst, "JPEG", quality=92, optimize=True)


def cost_for(model: str, quality: str, size: str) -> float:
    """Devuelve el coste estimado para esta combinacion."""
    return COST_PER_IMAGE.get((model, quality, size), 0.04)


async def generate_thumbnail(
    script: dict,
    out_path: Path,
    model: str = "dall-e-3",
    quality: str = "standard",
    size: str = "1024x1792",
    style_suffix: Optional[str] = None,
) -> Optional[dict]:
    """Genera un thumbnail con DALL-E para el script dado.

    Args:
        script: dict con 'title', 'hook', 'segments' (al menos uno).
        out_path: donde guardar el JPEG procesado a 1080x1920.
        model/quality/size: parametros DALL-E.
        style_suffix: sufijo del prompt (de policy_params.thumbnail_style_suffix).

    Returns:
        dict con info del thumbnail generado o None si fallo.
        {
          "path": Path,
          "prompt": str,
          "revised_prompt": str | None,  # DALL-E 3 reescribe el prompt
          "model": str,
          "cost_usd": float,
          "size": str,
        }
    """
    if not script:
        return None
    if not settings.openai_api_key:
        log.info("thumbnail_skipped reason=no_api_key")
        return None

    title = script.get("title") or ""
    hook = script.get("hook") or ""
    first_segment = (script.get("segments") or [{}])[0]
    mood = first_segment.get("mood") or "default"
    first_segment_kw = first_segment.get("keywords") or []

    style = (style_suffix or
             "cinematic horror, vertical 9:16, dramatic lighting, "
             "deep shadows, fog, eerie atmosphere, high contrast, "
             "no text, no watermarks, professional movie poster aesthetic")

    prompt = _build_prompt(title, hook, mood, first_segment_kw, style)
    log.info("thumbnail_generating model=%s size=%s quality=%s prompt_len=%d",
             model, size, quality, len(prompt))

    try:
        data = await _call_dalle(prompt, model, quality, size)
    except Exception as e:
        log.warning("thumbnail_dalle_failed err=%s", e)
        return None

    items = data.get("data") or []
    if not items:
        log.warning("thumbnail_dalle_empty data=%s", data)
        return None

    img_url = items[0].get("url")
    revised_prompt = items[0].get("revised_prompt")  # DALL-E 3 specific
    if not img_url:
        log.warning("thumbnail_dalle_no_url item=%s", items[0])
        return None

    # Descargar la imagen y procesarla
    raw_path = out_path.with_suffix(".raw.png")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            r = await client.get(img_url)
            r.raise_for_status()
            raw_path.write_bytes(r.content)
    except Exception as e:
        log.warning("thumbnail_download_failed err=%s", e)
        return None

    try:
        _resize_to_short(raw_path, out_path)
    except Exception as e:
        log.warning("thumbnail_resize_failed err=%s", e)
        return None
    finally:
        raw_path.unlink(missing_ok=True)

    cost = cost_for(model, quality, size)
    log.info("thumbnail_done path=%s cost=$%.3f", out_path, cost)

    return {
        "path": out_path,
        "prompt": prompt,
        "revised_prompt": revised_prompt,
        "model": model,
        "cost_usd": cost,
        "size": size,
        "quality": quality,
    }
