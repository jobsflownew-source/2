"""TTS service. Edge-TTS (free) primary, Azure as premium fallback."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Literal

import edge_tts
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import settings


# Mapeo voces es-ES en Edge -> aproximaciones (Edge usa los mismos nombres
# que Azure Neural en muchos casos: es-ES-AlvaroNeural, es-ES-ElviraNeural...)
DEFAULT_HORROR_RATE = "-8%"
DEFAULT_HORROR_PITCH = "-2Hz"


def _strip_for_tts(text: str) -> str:
    """Quita asteriscos, comillas y caracteres raros que arruinan la prosodia."""
    text = re.sub(r"[*_`]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


@retry(stop=stop_after_attempt(3),
       wait=wait_exponential(multiplier=1, min=2, max=10))
async def synthesize_edge_tts(
    text: str,
    out_path: str | Path,
    voice: str | None = None,
    rate: str = DEFAULT_HORROR_RATE,
    pitch: str = DEFAULT_HORROR_PITCH,
) -> tuple[Path, list[dict]]:
    """Sintetiza con Edge-TTS (gratis, sin clave). Devuelve path + word boundaries."""
    text = _strip_for_tts(text)
    voice = voice or settings.tts_default_voice
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    communicate = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch)
    boundaries: list[dict] = []

    with open(out_path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                boundaries.append({
                    "offset_ms": chunk["offset"] / 10_000,
                    "duration_ms": chunk["duration"] / 10_000,
                    "text": chunk["text"],
                })
    return out_path, boundaries


async def synthesize(
    text: str,
    out_path: str | Path,
    voice: str | None = None,
    provider: Literal["edge", "azure"] = "edge",
) -> tuple[Path, list[dict], str]:
    """Punto de entrada unificado. Devuelve (path, boundaries, provider_used)."""
    if provider == "edge":
        path, bounds = await synthesize_edge_tts(text, out_path, voice=voice)
        return path, bounds, "edge_tts"

    # Azure: solo si tenemos credenciales
    if provider == "azure" and settings.azure_speech_key:
        try:
            from .tts_azure import synthesize_azure
            return await synthesize_azure(text, out_path, voice=voice)
        except Exception:
            # fallback a edge
            path, bounds = await synthesize_edge_tts(text, out_path, voice=voice)
            return path, bounds, "edge_tts_fallback"

    path, bounds = await synthesize_edge_tts(text, out_path, voice=voice)
    return path, bounds, "edge_tts"


async def list_voices(language: str | None = None) -> list[dict]:
    """Lista voces disponibles en Edge-TTS, filtrables por idioma."""
    voices = await edge_tts.list_voices()
    if language:
        voices = [v for v in voices if v["Locale"].startswith(language)]
    return [
        {"name": v["ShortName"], "locale": v["Locale"], "gender": v["Gender"]}
        for v in voices
    ]


if __name__ == "__main__":  # smoke test manual
    async def _main():
        out, bounds, prov = await synthesize(
            "Aquella noche escuche pasos en el sotano. No habia nadie en casa.",
            "/tmp/test.mp3",
        )
        print(f"OK provider={prov} path={out} words={len(bounds)}")
    asyncio.run(_main())
