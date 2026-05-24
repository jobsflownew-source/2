"""Acceso a PostgreSQL con psycopg v3 (sync, envuelto en to_thread).

Funciones simples, sin ORM. El schema 'app' se asume existente (init.sql).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import settings


_pool: Optional[ConnectionPool] = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL no configurada")
        _pool = ConnectionPool(
            settings.database_url,
            min_size=1,
            max_size=5,
            kwargs={"row_factory": dict_row, "options": "-c search_path=app,public"},
        )
    return _pool


def _exec(query: str, params: tuple = (), fetch: str = "none") -> Any:
    """fetch: 'none' | 'one' | 'all'"""
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            if fetch == "one":
                return cur.fetchone()
            if fetch == "all":
                return cur.fetchall()
    return None


async def aexec(query: str, params: tuple = (), fetch: str = "none") -> Any:
    return await asyncio.to_thread(_exec, query, params, fetch)


# ------------------ Candidates ------------------
async def get_candidate(candidate_id: int) -> Optional[dict]:
    return await aexec(
        "SELECT id, source, external_id, author, title, selftext, language,"
        " theme, setting, tone, status, permalink, score, num_comments"
        " FROM story_candidates WHERE id = %s",
        (candidate_id,), fetch="one",
    )


async def pick_next_queued(limit: int = 1, language: Optional[str] = None) -> list[dict]:
    if language:
        return await aexec(
            "SELECT id, source, external_id, title, selftext, author,"
            " language, theme, setting, tone, score, num_comments"
            " FROM story_candidates"
            " WHERE status = 'queued' AND language = %s"
            " ORDER BY quality_score DESC NULLS LAST, fetched_at ASC"
            " LIMIT %s",
            (language, limit), fetch="all",
        ) or []
    return await aexec(
        "SELECT id, source, external_id, title, selftext, author,"
        " language, theme, setting, tone, score, num_comments"
        " FROM story_candidates"
        " WHERE status = 'queued'"
        " ORDER BY quality_score DESC NULLS LAST, fetched_at ASC"
        " LIMIT %s",
        (limit,), fetch="all",
    ) or []


async def mark_candidate_status(candidate_id: int, status: str,
                                error: Optional[str] = None) -> None:
    await aexec(
        "UPDATE story_candidates SET status = %s,"
        " rejection_reason = COALESCE(%s, rejection_reason)"
        " WHERE id = %s",
        (status, error, candidate_id),
    )


async def insert_ai_candidate(
    title: str, selftext: str, selftext_hash: str,
    language: str, theme: str, setting: str, tone: str,
    word_count: int, quality_score: float, horror_score: float,
    self_assessment: Optional[str], status: str,
    gen_model: str, gen_cost_usd: float,
    external_id: Optional[str] = None,
) -> int:
    row = await aexec(
        "INSERT INTO story_candidates (source, external_id, author, title,"
        " selftext, selftext_hash, language, theme, setting, tone, word_count,"
        " quality_score, horror_score, self_assessment, status,"
        " gen_model, gen_cost_usd)"
        " VALUES ('ai_generated', %s, 'AI', %s, %s, %s, %s, %s, %s, %s, %s,"
        "         %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (source, external_id) DO UPDATE"
        "   SET title = EXCLUDED.title"
        " RETURNING id",
        (external_id, title, selftext, selftext_hash, language,
         theme, setting, tone, word_count,
         quality_score, horror_score, self_assessment, status,
         gen_model, gen_cost_usd),
        fetch="one",
    )
    return row["id"]


async def stories_generated_today(language: Optional[str] = None) -> int:
    if language:
        row = await aexec(
            "SELECT COUNT(*) AS n FROM story_candidates"
            " WHERE source = 'ai_generated' AND language = %s"
            "   AND fetched_at >= DATE_TRUNC('day', now() AT TIME ZONE 'UTC')",
            (language,), fetch="one",
        )
    else:
        row = await aexec(
            "SELECT COUNT(*) AS n FROM story_candidates"
            " WHERE source = 'ai_generated'"
            "   AND fetched_at >= DATE_TRUNC('day', now() AT TIME ZONE 'UTC')",
            fetch="one",
        )
    return int(row["n"]) if row else 0


# ------------------ Scripts ------------------
async def get_script(candidate_id: int, language: str) -> Optional[dict]:
    return await aexec(
        "SELECT * FROM scripts WHERE candidate_id = %s AND language = %s",
        (candidate_id, language), fetch="one",
    )


async def insert_script(
    candidate_id: int, language: str, title: str, seo_description: str,
    tags: list[str], hook: str, segments: list[dict],
    total_estimated_sec: float, llm_model: str,
    tokens_in: int, tokens_out: int, cost_usd: float,
) -> int:
    row = await aexec(
        "INSERT INTO scripts (candidate_id, language, title, seo_description,"
        "  tags, hook, segments, total_estimated_sec, llm_model,"
        "  llm_input_tokens, llm_output_tokens, llm_cost_usd)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)"
        " ON CONFLICT (candidate_id, language) DO UPDATE"
        "   SET title = EXCLUDED.title,"
        "       seo_description = EXCLUDED.seo_description,"
        "       tags = EXCLUDED.tags,"
        "       hook = EXCLUDED.hook,"
        "       segments = EXCLUDED.segments,"
        "       total_estimated_sec = EXCLUDED.total_estimated_sec,"
        "       llm_cost_usd = EXCLUDED.llm_cost_usd"
        " RETURNING id",
        (candidate_id, language, title, seo_description, tags, hook,
         json.dumps(segments), total_estimated_sec, llm_model,
         tokens_in, tokens_out, cost_usd),
        fetch="one",
    )
    return row["id"]


# ------------------ Assets ------------------
async def insert_asset(
    script_id: int, segment_index: Optional[int], asset_type: str,
    source: str, storage_url: str,
    duration_ms: Optional[int] = None,
    width: Optional[int] = None, height: Optional[int] = None,
    file_size_bytes: Optional[int] = None,
    cost_usd: float = 0.0, meta: Optional[dict] = None,
) -> int:
    row = await aexec(
        "INSERT INTO assets (script_id, segment_index, type, source, storage_url,"
        "  duration_ms, width, height, file_size_bytes, cost_usd, meta)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)"
        " RETURNING id",
        (script_id, segment_index, asset_type, source, storage_url,
         duration_ms, width, height, file_size_bytes,
         cost_usd, json.dumps(meta or {})),
        fetch="one",
    )
    return row["id"]


# ------------------ Shorts ------------------
async def insert_short(
    script_id: int, language: str, title: str, description: str,
    tags: list[str], voice_id: str, image_style: Optional[str],
    final_video_url: str, duration_sec: float,
    file_size_bytes: int, status: str = "rendered",
) -> int:
    row = await aexec(
        "INSERT INTO shorts (script_id, language, title, description, tags,"
        "  voice_id, image_style, final_video_url, duration_sec,"
        "  file_size_bytes, status)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        " RETURNING id",
        (script_id, language, title, description, tags, voice_id, image_style,
         final_video_url, duration_sec, file_size_bytes, status),
        fetch="one",
    )
    return row["id"]


async def update_short_status(short_id: int, status: str,
                              error: Optional[str] = None) -> None:
    await aexec(
        "UPDATE shorts SET status = %s,"
        " error_message = COALESCE(%s, error_message)"
        " WHERE id = %s",
        (status, error, short_id),
    )


async def get_short(short_id: int) -> Optional[dict]:
    return await aexec(
        "SELECT id, script_id, language, voice_id, title, description, tags,"
        " final_video_url, duration_sec, file_size_bytes, status,"
        " youtube_video_id, channel_id, scheduled_for, published_at,"
        " error_message, created_at"
        " FROM shorts WHERE id = %s",
        (short_id,), fetch="one",
    )


async def pick_next_rendered(limit: int = 1, language: Optional[str] = None) -> list[dict]:
    """Shorts listos para subir (status='rendered', no subidos aun)."""
    if language:
        return await aexec(
            "SELECT id, script_id, language, title, description, tags,"
            " final_video_url, duration_sec, file_size_bytes"
            " FROM shorts"
            " WHERE status = 'rendered' AND language = %s"
            " ORDER BY created_at ASC"
            " LIMIT %s",
            (language, limit), fetch="all",
        ) or []
    return await aexec(
        "SELECT id, script_id, language, title, description, tags,"
        " final_video_url, duration_sec, file_size_bytes"
        " FROM shorts"
        " WHERE status = 'rendered'"
        " ORDER BY created_at ASC"
        " LIMIT %s",
        (limit,), fetch="all",
    ) or []


async def set_short_youtube(short_id: int, youtube_video_id: str,
                            channel_id: Optional[str] = None) -> None:
    """Marca el short como published con su video_id de YouTube."""
    await aexec(
        "UPDATE shorts SET status = 'published',"
        " youtube_video_id = %s,"
        " channel_id = COALESCE(%s, channel_id),"
        " published_at = now()"
        " WHERE id = %s",
        (youtube_video_id, channel_id, short_id),
    )


async def shorts_uploaded_today(language: Optional[str] = None) -> int:
    """Cuantos shorts se han subido a YouTube hoy (para respetar quota)."""
    if language:
        row = await aexec(
            "SELECT COUNT(*) AS n FROM shorts"
            " WHERE language = %s AND status = 'published'"
            "   AND published_at >= DATE_TRUNC('day', now() AT TIME ZONE 'UTC')",
            (language,), fetch="one",
        )
    else:
        row = await aexec(
            "SELECT COUNT(*) AS n FROM shorts"
            " WHERE status = 'published'"
            "   AND published_at >= DATE_TRUNC('day', now() AT TIME ZONE 'UTC')",
            fetch="one",
        )
    return int(row["n"]) if row else 0


# ------------------ Cost ledger ------------------
async def log_cost(service: str, operation: str, units: float,
                   cost_usd: float, script_id: Optional[int] = None,
                   short_id: Optional[int] = None,
                   meta: Optional[dict] = None) -> None:
    await aexec(
        "INSERT INTO cost_ledger (service, operation, units, cost_usd,"
        "  script_id, short_id, meta)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)",
        (service, operation, units, cost_usd, script_id, short_id,
         json.dumps(meta or {})),
    )


# ------------------ Daily limits ------------------
async def shorts_published_today(language: Optional[str] = None) -> int:
    if language:
        row = await aexec(
            "SELECT COUNT(*) AS n FROM shorts"
            " WHERE language = %s"
            "   AND status IN ('rendered','uploading','published')"
            "   AND created_at >= DATE_TRUNC('day', now() AT TIME ZONE 'UTC')",
            (language,), fetch="one",
        )
    else:
        row = await aexec(
            "SELECT COUNT(*) AS n FROM shorts"
            " WHERE status IN ('rendered','uploading','published')"
            "   AND created_at >= DATE_TRUNC('day', now() AT TIME ZONE 'UTC')",
            fetch="one",
        )
    return int(row["n"]) if row else 0


async def policy_get(key: str, default: Any = None) -> Any:
    row = await aexec(
        "SELECT value FROM policy_params WHERE key = %s",
        (key,), fetch="one",
    )
    return row["value"] if row else default


# ------------------ Voice rotation (multi-armed bandit) ------------------
async def pick_next_voice(language: str = "es") -> str:
    """Selecciona la siguiente voz por round-robin (menos usada primero).

    Lee policy_params.voices, filtra por idioma (matching prefix es-),
    y elige la que tiene menos pulls en bandit_arms. Cuando haya datos
    de retencion de YouTube se evolucionara a Thompson sampling con
    rewards_sum (eso es WF6_Optimizer, futuro).
    """
    voices = await policy_get("voices", default=["es-ES-AlvaroNeural"])
    if not isinstance(voices, list) or not voices:
        return "es-ES-AlvaroNeural"

    # Filtrar por idioma (es -> matching es-ES, es-MX, es-CO, etc.)
    lang_prefix = language.split("-")[0] if "-" in language else language
    matching = [v for v in voices if v.startswith(f"{lang_prefix}-")]
    if not matching:
        matching = voices

    # Round-robin justo: la de menos pulls, luego la mas antigua
    row = await aexec(
        "WITH pool AS (SELECT unnest(%s::text[]) AS voice),"
        " stats AS ("
        "  SELECT p.voice,"
        "         COALESCE(b.pulls, 0) AS pulls,"
        "         b.last_used"
        "  FROM pool p"
        "  LEFT JOIN bandit_arms b"
        "    ON b.arm_type = 'voice' AND b.arm_value = p.voice"
        " )"
        " SELECT voice FROM stats"
        " ORDER BY pulls ASC, last_used ASC NULLS FIRST"
        " LIMIT 1",
        (matching,), fetch="one",
    )
    return row["voice"] if row else matching[0]


async def record_voice_use(voice_id: str) -> None:
    """Registra +1 pull en bandit_arms para esta voz."""
    await aexec(
        "INSERT INTO bandit_arms (arm_type, arm_value, pulls, last_used)"
        " VALUES ('voice', %s, 1, now())"
        " ON CONFLICT (arm_type, arm_value)"
        " DO UPDATE SET pulls = bandit_arms.pulls + 1, last_used = now()",
        (voice_id,),
    )


# ------------------ Image deduplication ------------------
async def get_recent_image_urls(days: Optional[int] = None) -> set[str]:
    """Devuelve el set de URLs de imagenes ya usadas en los ultimos N dias.

    Si days es None, lee 'image_cooldown_days' de policy_params (default 30).
    Se usa para excluir candidatas en find_best_image y evitar reutilizar
    las mismas imagenes en shorts diferentes.
    """
    if days is None:
        cooldown = await policy_get("image_cooldown_days", default=30)
        try:
            days = int(cooldown)
        except (TypeError, ValueError):
            days = 30

    rows = await aexec(
        "SELECT image_url FROM used_images"
        " WHERE last_used_at >= now() - make_interval(days => %s)",
        (days,), fetch="all",
    ) or []
    return {r["image_url"] for r in rows if r.get("image_url")}


async def record_image_used(
    image_url: str, source: str,
    script_id: Optional[int] = None,
    segment_index: Optional[int] = None,
) -> None:
    """Registra que una URL de imagen externa fue usada.

    Si ya existia, incrementa times_used y actualiza last_used_at.
    Si es nueva, la inserta. Idempotente.
    """
    if not image_url:
        return
    await aexec(
        "INSERT INTO used_images"
        "  (image_url, source, first_used_at, last_used_at, times_used,"
        "   first_script_id, first_segment)"
        " VALUES (%s, %s, now(), now(), 1, %s, %s)"
        " ON CONFLICT (image_url) DO UPDATE"
        "   SET times_used = used_images.times_used + 1,"
        "       last_used_at = now()",
        (image_url, source, script_id, segment_index),
    )




# ------------------ TikTok publishing ------------------
async def pick_next_for_tiktok(limit: int = 1, language: Optional[str] = None) -> list[dict]:
    """Shorts listos para subir a TikTok.

    Selecciona los que estan rendered (al menos) y aun no publicados en
    TikTok. Acepta tambien los que ya estan publicados en YouTube
    (status='published') porque YouTube y TikTok son destinos paralelos.
    """
    where_lang = " AND s.language = %s" if language else ""
    args = [language, limit] if language else [limit]
    return await aexec(
        f"SELECT s.id, s.script_id, s.language, s.title, s.description, s.tags,"
        f" s.final_video_url, s.duration_sec, s.file_size_bytes, s.status,"
        f" s.tiktok_status, s.tiktok_video_id"
        f" FROM shorts s"
        f" WHERE s.status IN ('rendered','uploading','published')"
        f"   AND (s.tiktok_status IS NULL"
        f"        OR s.tiktok_status IN ('pending','failed'))"
        f"   {where_lang}"
        f" ORDER BY s.created_at ASC"
        f" LIMIT %s",
        tuple(args), fetch="all",
    ) or []


async def set_short_tiktok(
    short_id: int,
    publish_id: Optional[str] = None,
    video_id: Optional[str] = None,
    status: str = "published",
    url: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """Actualiza los campos tiktok_* de un short.

    Status validos: 'pending' | 'uploading' | 'published' | 'failed' | 'disabled'.
    Si status='published' graba published_at. Si status='failed' graba error.
    """
    await aexec(
        "UPDATE shorts SET"
        "  tiktok_publish_id = COALESCE(%s, tiktok_publish_id),"
        "  tiktok_video_id   = COALESCE(%s, tiktok_video_id),"
        "  tiktok_url        = COALESCE(%s, tiktok_url),"
        "  tiktok_status     = %s,"
        "  tiktok_published_at = CASE WHEN %s = 'published'"
        "                             THEN COALESCE(tiktok_published_at, now())"
        "                             ELSE tiktok_published_at END,"
        "  tiktok_error      = CASE WHEN %s = 'failed' THEN %s"
        "                           ELSE NULL END"
        " WHERE id = %s",
        (publish_id, video_id, url, status, status, status, error, short_id),
    )


async def tiktok_uploaded_today(language: Optional[str] = None) -> int:
    """Cuantos shorts se han publicado en TikTok hoy (cuota diaria)."""
    if language:
        row = await aexec(
            "SELECT COUNT(*) AS n FROM shorts"
            " WHERE language = %s"
            "   AND tiktok_status = 'published'"
            "   AND tiktok_published_at >= DATE_TRUNC('day', now() AT TIME ZONE 'UTC')",
            (language,), fetch="one",
        )
    else:
        row = await aexec(
            "SELECT COUNT(*) AS n FROM shorts"
            " WHERE tiktok_status = 'published'"
            "   AND tiktok_published_at >= DATE_TRUNC('day', now() AT TIME ZONE 'UTC')",
            fetch="one",
        )
    return int(row["n"]) if row else 0
