"""Generador de historias originales de terror con GPT.

Reemplaza la ingesta de Reddit. Genera contenido 100% original con:
- Bancos rotativos de themes / settings / tones (configurables en policy_params)
- Auto-evaluacion (self-score) para calidad y horror
- Sin gore, sin contenido NSFW, apto para YouTube Shorts
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from typing import Any, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import settings


MODEL_COSTS = {
    "gpt-4o-mini": {"in": 0.15, "out": 0.60},
    "gpt-4o": {"in": 2.50, "out": 10.00},
}

LANGUAGE_LABELS = {
    "es": "espanol castellano natural",
    "en": "english",
    "pt": "portugues brasileiro",
    "fr": "francais",
}

# Bancos por defecto (override via policy_params)
DEFAULT_THEMES = [
    "ritual antiguo redescubierto por accidente",
    "entidad atrapada en un objeto cotidiano",
    "doble identico que reemplaza al original",
    "transmision que no deberia existir",
    "bucle temporal con reglas crueles",
    "trabajo nocturno con normas que no debes romper",
    "foto o grabacion que cambia con el tiempo",
    "vecino o conocido que no es lo que parece",
    "infancia que regresa con un significado oscuro",
    "promesa hecha hace anos que se cobra ahora",
    "juego inocente con consecuencias catastroficas",
    "presencia que solo aparece a una hora especifica",
    "regla familiar nunca explicada que ahora entiende",
    "testigo de algo que el resto no recuerda",
    "casa que cambia su geometria por la noche",
]

DEFAULT_SETTINGS = [
    "metro de madrugada vacio",
    "cabana en el bosque sin senal",
    "hospital de noche pasillo cerrado",
    "carretera secundaria entre niebla",
    "edificio de oficinas piso 13 fuera de horario",
    "habitacion de hotel de carretera",
    "sotano de casa familiar",
    "garaje subterraneo a las 4am",
    "supermercado 24h sin clientes",
    "playa abandonada en invierno",
    "iglesia rural cerrada por restauracion",
    "camping fuera de temporada",
    "biblioteca antigua seccion restringida",
    "estacion de tren rural sin personal",
    "pueblo de montana desconectado por nieve",
]

DEFAULT_TONES = [
    "psicologico sutil",
    "sobrenatural mundano",
    "creeping dread",
    "folk horror",
    "liminal",
]


SYSTEM_PROMPT = """Eres un escritor profesional de horror tipo nosleep / creepypasta. Escribes historias originales en primera persona, en {language_label}, con voz natural conversacional.

Reglas estrictas:
- Voz natural conversacional, como si el narrador hablara con un amigo.
- Detalles concretos (marcas, horas exactas, edades, oficios, ciudades genericas o reales pero comunes).
- NUNCA gore explicito, sangre detallada, contenido sexual, ni menores en peligro.
- Estructura: enganche fuerte (3-5 frases), desarrollo creciente, twist o revelacion al final.
- Sin separadores tipo "---", sin epigrafes, sin numeracion de capitulos.
- Sin elementos meta tipo "esta es mi historia" o "no se si me creeran".
- IMPORTANTE: La historia DEBE alcanzar la longitud minima solicitada. Desarrolla escenas, descripciones, monologo interior y dialogos hasta llegar al rango pedido. NUNCA termines antes de la longitud minima.
- Devuelves SOLO JSON valido, sin markdown ni explicaciones."""


USER_TEMPLATE = """Escribe una historia ORIGINAL de terror. NO copies de fuentes existentes.

Idioma: {language_label}
Tema central: {theme}
Setting: {setting}
Tono: {tone}

LONGITUD OBLIGATORIA: la historia debe tener entre {min_words} y {max_words} palabras.
Si te quedas corto, expande con descripcion sensorial, monologo interior, dialogo y construccion de atmosfera. Cuenta las palabras antes de devolver.

Devuelve EXACTAMENTE este JSON:
{{
  "title": "titulo en {language_label}, max 80 chars, atractivo sin ser clickbait",
  "story": "historia completa, primera persona, sin titulo dentro del texto, sin separadores, sin epigrafes. MINIMO {min_words} palabras.",
  "self_quality_score": 1-10 (autocritica honesta de la calidad narrativa),
  "self_horror_score": 1-10 (cuanto miedo o tension genera),
  "self_assessment": "una frase corta de autocritica honesta",
  "core_image_keywords": ["3-5 keywords en INGLES para buscar imagen base"]
}}"""


def _strip_to_json(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _word_count(text: str) -> int:
    return len(text.split())


def _hash_story(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _calc_cost(model: str, usage: dict) -> float:
    rates = MODEL_COSTS.get(model) or MODEL_COSTS["gpt-4o-mini"]
    return round(
        (usage.get("prompt_tokens", 0) * rates["in"] / 1e6) +
        (usage.get("completion_tokens", 0) * rates["out"] / 1e6),
        5,
    )


@retry(stop=stop_after_attempt(3),
       wait=wait_exponential(multiplier=2, min=2, max=20),
       reraise=True)
async def _call_openai(messages: list[dict], model: str,
                       temperature: float = 0.85) -> tuple[str, dict]:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY no configurada")

    payload = {
        "model": model,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "messages": messages,
        "max_tokens": 6000,  # historias de hasta ~4000 palabras con margen
    }
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(
            "https://api.openai.com/v1/chat/completions",
            json=payload, headers=headers,
        )
        r.raise_for_status()
        data = r.json()
    return data["choices"][0]["message"]["content"], data.get("usage", {})


MIN_WORDS_HARD_FLOOR = 500   # piso absoluto, debajo es inutil aunque haya hueco
MAX_WORDS_HARD_CEILING = 4500  # techo absoluto, arriba es inviable de procesar


def _validate_story(parsed: dict, min_words: int = MIN_WORDS_HARD_FLOOR,
                    max_words: int = MAX_WORDS_HARD_CEILING) -> tuple[bool, str | None]:
    required = ["title", "story", "self_quality_score", "self_horror_score"]
    for k in required:
        if k not in parsed:
            return False, f"missing_field:{k}"
    if len(parsed["title"]) > 100:
        return False, "title_too_long"
    wc = _word_count(parsed["story"])
    if wc < min_words:
        return False, f"story_too_short:{wc}"
    if wc > max_words:
        return False, f"story_too_long:{wc}"
    # Filtro basico de contenido prohibido (capa extra, GPT ya lo hace)
    forbidden = ["niño", "menor de edad", "violacion", "incesto"]
    low = parsed["story"].lower()
    for f in forbidden:
        if f in low:
            return False, f"forbidden_content:{f}"
    return True, None


async def generate_story(
    language: str = "es",
    theme: Optional[str] = None,
    setting: Optional[str] = None,
    tone: Optional[str] = None,
    themes_pool: Optional[list[str]] = None,
    settings_pool: Optional[list[str]] = None,
    tones_pool: Optional[list[str]] = None,
    min_words: int = 1200,
    max_words: int = 2800,
    model: Optional[str] = None,
    seed_id: Optional[str] = None,
) -> dict:
    """Genera una historia original. Devuelve dict con story + meta.

    Si GPT genera muy corto (suele pasar con gpt-4o-mini), reintenta una
    vez pidiendo expansion explicita antes de fallar.
    """
    model = model or settings.openai_model_cheap
    themes = themes_pool or DEFAULT_THEMES
    sets = settings_pool or DEFAULT_SETTINGS
    tones = tones_pool or DEFAULT_TONES

    chosen_theme = theme or random.choice(themes)
    chosen_setting = setting or random.choice(sets)
    chosen_tone = tone or random.choice(tones)

    user_msg = USER_TEMPLATE.format(
        language_label=LANGUAGE_LABELS.get(language, "espanol"),
        theme=chosen_theme,
        setting=chosen_setting,
        tone=chosen_tone,
        min_words=min_words,
        max_words=max_words,
    )
    sys_msg = SYSTEM_PROMPT.format(
        language_label=LANGUAGE_LABELS.get(language, "espanol"),
    )
    messages = [
        {"role": "system", "content": sys_msg},
        {"role": "user", "content": user_msg},
    ]

    content, usage = await _call_openai(messages, model)
    total_in_tokens = usage.get("prompt_tokens", 0)
    total_out_tokens = usage.get("completion_tokens", 0)

    try:
        parsed = json.loads(_strip_to_json(content))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"GPT devolvio JSON invalido: {e}") from e

    # Validacion con piso absoluto (MIN_WORDS_HARD_FLOOR=500)
    ok, err = _validate_story(parsed)

    # Auto-retry una vez si la historia es corta (GPT-4o-mini suele acortar)
    if not ok and err and err.startswith("story_too_short:") and parsed.get("story"):
        current_wc = _word_count(parsed["story"])
        target_extra = max(min_words - current_wc, 600)
        expand_user_msg = (
            f"La historia anterior tiene {current_wc} palabras pero necesito al "
            f"menos {min_words}. Reescribela COMPLETA expandiendo escenas con "
            f"mas descripcion sensorial, monologo interior, dialogo y atmosfera, "
            f"manteniendo el mismo argumento y final. Anade aproximadamente "
            f"{target_extra} palabras adicionales repartidas por toda la "
            "narracion (no solo al final). Devuelve el mismo JSON con la "
            "historia ampliada."
        )
        retry_messages = messages + [
            {"role": "assistant", "content": content},
            {"role": "user", "content": expand_user_msg},
        ]
        content2, usage2 = await _call_openai(retry_messages, model)
        total_in_tokens += usage2.get("prompt_tokens", 0)
        total_out_tokens += usage2.get("completion_tokens", 0)
        try:
            parsed2 = json.loads(_strip_to_json(content2))
            # Mantenemos el self_assessment del primer intento si el retry no lo trae
            for fb_field in ("self_quality_score", "self_horror_score",
                             "self_assessment", "core_image_keywords"):
                if fb_field not in parsed2 and fb_field in parsed:
                    parsed2[fb_field] = parsed[fb_field]
            parsed = parsed2
            ok, err = _validate_story(parsed)
        except json.JSONDecodeError:
            # nos quedamos con el original; ok seguira False
            pass

    if not ok:
        raise RuntimeError(f"Historia rechazada por validador: {err}")

    cost = _calc_cost(model, {
        "prompt_tokens": total_in_tokens,
        "completion_tokens": total_out_tokens,
    })

    return {
        "title": parsed["title"],
        "story": parsed["story"],
        "self_quality_score": float(parsed.get("self_quality_score", 0)),
        "self_horror_score": float(parsed.get("self_horror_score", 0)),
        "self_assessment": parsed.get("self_assessment"),
        "core_image_keywords": parsed.get("core_image_keywords", []),
        "_meta": {
            "language": language,
            "theme": chosen_theme,
            "setting": chosen_setting,
            "tone": chosen_tone,
            "word_count": _word_count(parsed["story"]),
            "selftext_hash": _hash_story(parsed["story"]),
            "model": model,
            "tokens_in": total_in_tokens,
            "tokens_out": total_out_tokens,
            "cost_usd": cost,
            "seed_id": seed_id,
        },
    }
