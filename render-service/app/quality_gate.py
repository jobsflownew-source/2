"""Quality gate con GPT-4o-mini.

Tras producir el MP4, evaluamos el short con LLM:
  - Coherencia narrativa
  - Hook engaging
  - Mood consistency entre segmentos
  - Tags relevantes
  - Cliffhanger / twist al final

El modelo emite {verdict, score 0-10, reasoning}. Si score < threshold,
el pipeline marca el short como 'low_quality' y NO entra en la cola
de publicacion (status='rendered' se queda fuera del WHERE de WF4/WF5).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import settings


log = logging.getLogger(__name__)


# Costes USD por 1M tokens (chat completions)
MODEL_COSTS = {
    "gpt-4o-mini": {"in": 0.15, "out": 0.60},
    "gpt-4o": {"in": 2.50, "out": 10.00},
}


SYSTEM_PROMPT = """You are a senior YouTube Shorts quality reviewer for a
horror channel. You score short scripts on a 0-10 scale based on:

  - Hook strength (0-3 points): Does the first sentence make me want to keep watching?
  - Narrative coherence (0-3 points): Does the story make sense and build tension?
  - Mood consistency (0-2 points): Are tone/mood/pacing consistent?
  - Closure (0-2 points): Does the ending land with a twist, cliffhanger or question?

Penalize: generic openings ("Hola", "Esta es la historia"), confusing jumps,
weak endings, padding, repetitive phrasing, clickbait without payoff.

You output STRICT JSON only, no markdown, no explanations outside JSON."""


USER_PROMPT_TEMPLATE = """Evaluate this YouTube Short script for a horror channel.

TITLE: {title}
HOOK: {hook}
DURATION: {duration_sec}s ({segments_count} segments)
VOICE: {voice}
TAGS: {tags}

SEGMENTS:
{segments_text}

Return JSON ONLY:
{{
  "verdict": "publish" | "reject",
  "score": <int 0-10>,
  "reasoning": "<one short sentence>"
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


def _build_segments_text(segments: list[dict]) -> str:
    """Resume cada segmento en una linea para no inflar el prompt."""
    lines = []
    for i, seg in enumerate(segments[:6]):  # max 6 segmentos
        text = (seg.get("text") or "")[:300]
        mood = seg.get("mood") or "default"
        lines.append(f"[seg {i} mood={mood}] {text}")
    return "\n".join(lines)


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1.5, min=1, max=8),
    reraise=True,
)
async def _call_openai(messages: list[dict], model: str) -> tuple[str, dict]:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY no configurada")

    payload = {
        "model": model,
        "temperature": 0.3,
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


async def evaluate_short(
    title: str,
    hook: str,
    segments: list[dict],
    duration_sec: float,
    voice: str,
    tags: list[str],
    model: str = "gpt-4o-mini",
) -> Optional[dict]:
    """Evalua la calidad del short con GPT-4o-mini.

    Returns dict con {verdict, score, reasoning, cost_usd, model, tokens_in,
    tokens_out} o None si fallo / sin API key.
    """
    if not settings.openai_api_key:
        log.info("quality_gate_skipped reason=no_api_key")
        return None

    user_text = USER_PROMPT_TEMPLATE.format(
        title=title.strip()[:200],
        hook=(hook or "").strip()[:300],
        duration_sec=round(duration_sec or 0, 1),
        segments_count=len(segments),
        voice=voice or "unknown",
        tags=", ".join(tags[:10]) if tags else "(none)",
        segments_text=_build_segments_text(segments),
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]

    try:
        content, usage = await _call_openai(messages, model)
    except Exception as e:
        log.warning("quality_gate_call_failed err=%s", e)
        return None

    try:
        parsed = json.loads(_strip_to_json(content))
        verdict = str(parsed.get("verdict", "publish")).lower()
        score = float(parsed.get("score", 0))
        reasoning = str(parsed.get("reasoning", ""))[:500]
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        log.warning("quality_gate_parse_failed err=%s content=%s",
                    e, content[:200])
        return None

    if verdict not in ("publish", "reject"):
        verdict = "publish"
    score = max(0.0, min(score, 10.0))

    cost = _calc_cost(model, usage)
    log.info("quality_gate_ok model=%s verdict=%s score=%.1f cost=$%s",
             model, verdict, score, cost)

    return {
        "verdict": verdict,
        "score": score,
        "reasoning": reasoning,
        "model": model,
        "cost_usd": cost,
        "tokens_in": usage.get("prompt_tokens", 0),
        "tokens_out": usage.get("completion_tokens", 0),
    }
