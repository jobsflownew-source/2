"""Seleccion inteligente de imagenes con GPT-4o-mini vision.

Dado un segmento narrativo (texto + mood + keywords) y una lista de
candidatas (urls + metadata), pide a la IA que elija la que mejor
transmite la atmosfera y devuelve la elegida + razonamiento.

Si OPENAI_API_KEY no esta configurada o el modelo falla, devuelve
None y el pipeline cae al random-pick de _filter_and_pick.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import settings


log = logging.getLogger(__name__)


# Costes por modelo (USD por 1M tokens). Vision low-detail = 85 tokens/img.
MODEL_COSTS = {
    "gpt-4o-mini": {"in": 0.15, "out": 0.60},
    "gpt-4o": {"in": 2.50, "out": 10.00},
}


SYSTEM_PROMPT = """You are a senior video director selecting stock photos for
a horror YouTube Short. You evaluate candidates against a narrative segment
and choose the one that best transmits its mood, scene and emotional tone.

Selection criteria, ranked by importance:
1. Mood match: lighting, color palette and atmosphere align with the segment
   (tension, fear, despair, reveal, aftermath).
2. Scene relevance: visual elements match the keywords and what is described.
3. Cinematic quality: framing, depth, contrast suitable for a vertical 9:16
   horror short. Cliched / generic stock photos are penalized.
4. Vertical orientation friendliness: portrait crops better than landscape.

You return STRICT JSON, no markdown, no extra text."""


USER_PROMPT_TEMPLATE = """Pick the BEST image for this narrative segment.

SEGMENT TEXT:
\"\"\"{segment_text}\"\"\"

MOOD: {mood}
KEYWORDS: {keywords}

CANDIDATES (in order):
{candidate_lines}

Return JSON ONLY in this exact shape (no markdown):
{{
  "chosen_index": <0-based int into candidates>,
  "score": <int 1-10 quality of the chosen one>,
  "reasoning": "<one short sentence explaining the choice>"
}}"""


def _calc_cost(model: str, usage: dict) -> float:
    rates = MODEL_COSTS.get(model) or MODEL_COSTS["gpt-4o-mini"]
    return round(
        (usage.get("prompt_tokens", 0) * rates["in"] / 1e6) +
        (usage.get("completion_tokens", 0) * rates["out"] / 1e6),
        6,
    )


def _strip_to_json(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _build_messages(
    segment_text: str, mood: str, keywords: list[str],
    candidates: list[dict],
) -> list[dict]:
    """Construye los mensajes multimodales para el endpoint chat/completions.

    Cada candidata se anade como un image_url con detail=low (85 tokens),
    suficiente para juzgar mood/composicion sin disparar el coste.
    """
    candidate_lines = "\n".join(
        f"  [{i}] keywords={c.get('keywords_query','')} "
        f"size={c.get('width','?')}x{c.get('height','?')} "
        f"source={c.get('source','?')}"
        for i, c in enumerate(candidates)
    )
    text_part = USER_PROMPT_TEMPLATE.format(
        segment_text=segment_text[:600],
        mood=mood,
        keywords=", ".join(keywords[:5]) or "(none)",
        candidate_lines=candidate_lines,
    )

    user_content: list[dict] = [{"type": "text", "text": text_part}]
    for c in candidates:
        url = c.get("url") or c.get("download")
        if not url:
            continue
        user_content.append({
            "type": "image_url",
            "image_url": {"url": url, "detail": "low"},
        })

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1.5, min=1, max=8),
    reraise=True,
)
async def _call_openai_vision(messages: list[dict], model: str) -> tuple[str, dict]:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY no configurada")

    payload = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 200,
        "response_format": {"type": "json_object"},
        "messages": messages,
    }
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.openai.com/v1/chat/completions",
            json=payload, headers=headers,
        )
        r.raise_for_status()
        data = r.json()
    return data["choices"][0]["message"]["content"], data.get("usage", {})


async def select_best_image_with_ai(
    segment_text: str,
    mood: str,
    keywords: list[str],
    candidates: list[dict],
    model: Optional[str] = None,
) -> Optional[dict]:
    """Pide a GPT-4o-mini vision que elija la mejor candidata.

    Returns:
        dict con la imagen elegida (mismo shape que las candidatas) +
        un campo extra 'ai_selection' con score/reasoning/cost, o None
        si la IA falla o no hay candidatas validas.
    """
    if not candidates:
        return None
    if not settings.openai_api_key:
        log.info("ai_image_selection_skipped reason=no_api_key")
        return None
    # Si solo hay 1, no tiene sentido gastar tokens
    if len(candidates) == 1:
        only = dict(candidates[0])
        only["ai_selection"] = {"chosen_index": 0, "score": 0,
                                "reasoning": "single_candidate", "cost_usd": 0.0}
        return only

    model = model or "gpt-4o-mini"
    messages = _build_messages(segment_text, mood, keywords, candidates)

    try:
        content, usage = await _call_openai_vision(messages, model)
    except Exception as e:
        log.warning("ai_image_selection_failed err=%s candidates=%d",
                    e, len(candidates))
        return None

    try:
        parsed = json.loads(_strip_to_json(content))
        idx = int(parsed.get("chosen_index", 0))
        score = int(parsed.get("score", 0))
        reasoning = str(parsed.get("reasoning", ""))[:300]
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        log.warning("ai_image_selection_parse_failed err=%s content=%s",
                    e, content[:200])
        return None

    if idx < 0 or idx >= len(candidates):
        log.warning("ai_image_selection_bad_index idx=%s n=%d", idx, len(candidates))
        idx = 0

    chosen = dict(candidates[idx])
    chosen["ai_selection"] = {
        "model": model,
        "candidates_count": len(candidates),
        "chosen_index": idx,
        "score": score,
        "reasoning": reasoning,
        "cost_usd": _calc_cost(model, usage),
        "tokens_in": usage.get("prompt_tokens", 0),
        "tokens_out": usage.get("completion_tokens", 0),
    }
    log.info(
        "ai_image_selection_ok model=%s idx=%d/%d score=%d cost=$%s",
        model, idx, len(candidates), score, chosen["ai_selection"]["cost_usd"],
    )
    return chosen
