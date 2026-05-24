"""Generador de guion segmentado con OpenAI.

Convierte una historia de Reddit en JSON estructurado listo para producir
un Short: title, hook, segments con texto narrativo, keywords e image_prompt.
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import settings


# Costes aproximados (USD por 1M tokens)
MODEL_COSTS = {
    "gpt-4o-mini": {"in": 0.15, "out": 0.60},
    "gpt-4o": {"in": 2.50, "out": 10.00},
    "gpt-4o-mini-2024-07-18": {"in": 0.15, "out": 0.60},
}


SYSTEM_PROMPT = """Eres un guionista profesional especializado en historias de terror para YouTube Shorts y TikTok.
Adaptas historias al formato de video corto vertical (60s max por segmento) optimizando RETENCION.

Reglas estrictas:
- Devuelves SOLO JSON valido, sin explicaciones ni markdown.
- El idioma del guion es el indicado por el usuario.
- Conservas atmosfera, tension y el twist final.
- NO usas comillas dramaticas, asteriscos ni emojis en el texto narrativo.
- NO incluyes "Capitulo X" ni numeracion verbal en el texto.
- Cada segmento debe terminar en pausa narrativa natural (punto o cliffhanger).
- Las keywords y el image_prompt SIEMPRE en INGLES (para APIs de imagenes).
- El image_prompt es descriptivo, cinematografico, vertical 9:16, sin texto.

REGLAS DEL HOOK VIRAL (primer segmento, los primeros 3 segundos son los mas importantes):
- DEBE empezar con una declaracion fuerte que despierte curiosidad inmediata.
- Usa uno de estos patrones probados (varia entre shorts):
    a) "Mi <familiar> nunca me dijo por que <prohibicion concreta>... hasta que <verbo en pasado>."
    b) "Pense que <suposicion comun> hasta que <evento perturbador concreto>."
    c) "Si alguna vez <accion cotidiana>, esto es lo que NO debes hacer."
    d) "<Numero> de <unidad de tiempo> despues, todavia no puedo explicar lo que <verbo>."
    e) "Esto pasa cuando <accion> a las <hora especifica> en <lugar concreto>."
- EVITA: "Hola", "Soy", "Esta es la historia de", "Era un dia normal", clickbait obvio.
- El hook DEBE plantear una incognita que solo se resuelve viendo el resto.
- 12-25 palabras maximo. Frases cortas. Sin adjetivos vacios."""


USER_TEMPLATE = """Adapta esta historia a un YouTube Short narrado en {language_label}.

TITULO ORIGINAL: {title}
{source_line}HISTORIA:
{story}

Devuelve EXACTAMENTE este JSON:
{{
  "title": "string max 70 chars, atractivo, sin clickbait extremo",
  "seo_description": "string max 200 chars con #shorts #horror #nosleep al final",
  "tags": ["array max 8 tags lowercase sin #"],
  "hook": "frase narrativa de 3-8 segundos hablados, en {language_label}, siguiendo las REGLAS DEL HOOK VIRAL del system prompt",
  "segments": [
    {{
      "id": 1,
      "text": "texto narrativo en {language_label}, 35-55 segundos hablados (90-140 palabras), sin comillas dramaticas",
      "estimated_duration_sec": 45,
      "keywords": ["english", "keywords", "for", "image", "search", "max 5"],
      "image_prompt": "english cinematic horror description, vertical 9:16, no text",
      "mood": "tension|fear|despair|reveal|aftermath"
    }}
  ]
}}

Reglas adicionales:
- Genera entre 3 y 5 segmentos. Total entre 120 y 240 segundos.
- El primer segmento ES el hook viral expandido a narracion (NO repitas el hook palabra por palabra; el primer segmento de 'segments' debe COMENZAR con el hook y luego desarrollarlo durante 35-55 segundos).
- El SEGUNDO segmento debe terminar con un mini-cliffhanger ("y entonces vi que...", "lo que no esperaba era...") para reforzar retencion en 30-60s.
- El UNTERMOST segmento (el ultimo) cierra con un giro o pregunta abierta que genere comentarios.
- Calcula estimated_duration_sec asumiendo {wpm} palabras por minuto.{attribution_rule}
- Si la historia es inadaptable (muy confusa, sin trama, demasiado corta), devuelve {{"reject": true, "reason": "..."}} en lugar del JSON normal."""


LANGUAGE_LABELS = {
    "es": "espanol castellano natural",
    "en": "english",
    "pt": "portugues brasileiro",
    "fr": "francais",
}

LANGUAGE_WPM = {"es": 145, "en": 155, "pt": 145, "fr": 150}


def _strip_to_json(text: str) -> str:
    """Quita markdown fences si GPT los puso por error."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _build_user_prompt(candidate: dict, language: str) -> str:
    story = candidate["selftext"]
    if len(story) > 6000:
        story = story[:6000] + "\n\n[...resto truncado...]"

    source = candidate.get("source", "ai_generated")
    if source == "reddit":
        source_line = f"AUTOR: u/{candidate.get('author', 'anonymous')}\n"
        attribution_rule = (
            f"\n- Atribuye al autor en seo_description: "
            f"\"Story by u/{candidate.get('author', 'anonymous')}\"."
        )
    else:
        source_line = ""
        attribution_rule = ""

    return USER_TEMPLATE.format(
        title=candidate["title"],
        source_line=source_line,
        attribution_rule=attribution_rule,
        story=story,
        language_label=LANGUAGE_LABELS.get(language, "espanol"),
        wpm=LANGUAGE_WPM.get(language, 145),
    )


@retry(stop=stop_after_attempt(3),
       wait=wait_exponential(multiplier=2, min=2, max=20),
       reraise=True)
async def call_openai(messages: list[dict], model: str) -> tuple[str, dict]:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY no configurada")

    payload = {
        "model": model,
        "temperature": 0.7,
        "response_format": {"type": "json_object"},
        "messages": messages,
    }
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(
            "https://api.openai.com/v1/chat/completions",
            json=payload, headers=headers,
        )
        r.raise_for_status()
        data = r.json()
    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    return content, usage


def _calc_cost(model: str, usage: dict) -> float:
    rates = MODEL_COSTS.get(model) or MODEL_COSTS["gpt-4o-mini"]
    return round(
        (usage.get("prompt_tokens", 0) * rates["in"] / 1e6) +
        (usage.get("completion_tokens", 0) * rates["out"] / 1e6),
        5,
    )


def _validate_script(parsed: dict, language: str) -> tuple[bool, str | None]:
    if parsed.get("reject"):
        return False, parsed.get("reason", "rejected_by_model")
    required = ["title", "seo_description", "tags", "hook", "segments"]
    for k in required:
        if k not in parsed:
            return False, f"missing_field:{k}"
    if not isinstance(parsed["segments"], list) or not parsed["segments"]:
        return False, "no_segments"
    if len(parsed["segments"]) > 6:
        return False, "too_many_segments"

    # Sanity check sobre cada segmento
    wpm = LANGUAGE_WPM.get(language, 145)
    total = 0.0
    for i, seg in enumerate(parsed["segments"]):
        for sk in ("text", "keywords", "image_prompt"):
            if sk not in seg:
                return False, f"segment_{i}_missing:{sk}"
        # recalcula duracion estimada con wpm consistente
        words = len(seg["text"].split())
        seg["estimated_duration_sec"] = round(words / wpm * 60, 1)
        total += seg["estimated_duration_sec"]
        if seg["estimated_duration_sec"] > 65:
            return False, f"segment_{i}_too_long"
    parsed["total_estimated_sec"] = round(total, 1)
    if total > 280:  # margen sobre 60s*4
        return False, "total_too_long"
    return True, None


async def generate_script(candidate: dict, language: str = "es",
                          model: str | None = None) -> dict:
    """Genera un guion estructurado a partir de un candidate dict."""
    model = model or settings.openai_model_cheap
    user_msg = _build_user_prompt(candidate, language)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    content, usage = await call_openai(messages, model)
    cost = _calc_cost(model, usage)

    try:
        parsed = json.loads(_strip_to_json(content))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"GPT devolvio JSON invalido: {e}") from e

    ok, err = _validate_script(parsed, language)
    if not ok:
        raise RuntimeError(f"Guion rechazado: {err}")

    parsed["_meta"] = {
        "model": model,
        "tokens_in": usage.get("prompt_tokens", 0),
        "tokens_out": usage.get("completion_tokens", 0),
        "cost_usd": cost,
    }
    return parsed
