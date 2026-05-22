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
        "SELECT id, reddit_id, subreddit, author, title, selftext, score,"
        " num_comments, status, permalink"
        " FROM reddit_candidates WHERE id = %s",
        (candidate_id,), fetch="one",
    )


async def pick_next_queued(limit: int = 1) -> list[dict]:
    return await aexec(
        "SELECT id, reddit_id, title, selftext, author, score, num_comments"
        " FROM reddit_candidates"
        " WHERE status = 'queued'"
        " ORDER BY quality_score DESC NULLS LAST, score DESC"
        " LIMIT %s",
        (limit,), fetch="all",
    ) or []


async def mark_candidate_status(candidate_id: int, status: str,
                                error: Optional[str] = None) -> None:
    await aexec(
        "UPDATE reddit_candidates SET status = %s,"
        " rejection_reason = COALESCE(%s, rejection_reason)"
        " WHERE id = %s",
        (status, error, candidate_id),
    )


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
