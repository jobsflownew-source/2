"""Cliente de TikTok Content Posting API.

Soporta dos modos de publicacion (configurables via policy_params):

1. DIRECT_INBOX  (default, scope video.upload, NO requiere app audit)
   El video aparece en el inbox/drafts del creador. El creador debe
   abrir TikTok y darle "Post" para publicarlo. Util para reviews
   manuales o como bootstrap antes de pasar el audit.

2. DIRECT_POST   (scope video.publish, requiere app audit por TikTok)
   Publica directamente en el feed con la privacidad configurada.
   Aprobacion suele tardar 1-2 semanas tras enviar la app a review.

OAuth con refresh_token (sin browser tras setup inicial):
   Tokens de acceso de TikTok caducan en 24h. El refresh_token caduca
   en 365 dias y se rota cada vez que se usa, por lo que escribimos el
   nuevo refresh_token en .env.local cada vez que rotamos (best effort).

Setup inicial: scripts/tiktok_oauth.py.

Subida del video: usamos el modo FILE_UPLOAD con chunked upload.
TikTok exige chunks alineados a 5 MB minimo (excepto el ultimo).
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import settings

log = logging.getLogger(__name__)


# Endpoints
TIKTOK_API = "https://open.tiktokapis.com"
OAUTH_TOKEN_URL = f"{TIKTOK_API}/v2/oauth/token/"
INBOX_INIT_URL = f"{TIKTOK_API}/v2/post/publish/inbox/video/init/"
POST_INIT_URL = f"{TIKTOK_API}/v2/post/publish/video/init/"
STATUS_FETCH_URL = f"{TIKTOK_API}/v2/post/publish/status/fetch/"
CREATOR_INFO_URL = f"{TIKTOK_API}/v2/post/publish/creator_info/query/"

# Chunked upload parameters (TikTok rules: 5 MB <= chunk < 64 MB,
# total parts <= 1000, last part puede ser menor que 5 MB).
CHUNK_SIZE = 10 * 1024 * 1024  # 10 MB

# Timeouts
HTTP_TIMEOUT = 60
UPLOAD_TIMEOUT = 300


# ---------- OAuth ----------

@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1.5, min=1, max=8),
    reraise=True,
)
async def _refresh_access_token() -> dict:
    """Intercambia el refresh_token por un access_token + nuevo refresh_token.

    TikTok rota el refresh_token en cada uso. Lo nuevo se devuelve en la
    respuesta. Lo guardamos en memoria para esta ejecucion y best-effort
    intentamos persistirlo en .env.local (no critico si falla).
    """
    if not settings.tiktok_client_key:
        raise RuntimeError("TIKTOK_CLIENT_KEY no configurado")
    if not settings.tiktok_client_secret:
        raise RuntimeError("TIKTOK_CLIENT_SECRET no configurado")
    if not settings.tiktok_refresh_token:
        raise RuntimeError(
            "TIKTOK_REFRESH_TOKEN no configurado. "
            "Ejecuta scripts/tiktok_oauth.py para obtenerlo."
        )

    payload = {
        "client_key": settings.tiktok_client_key,
        "client_secret": settings.tiktok_client_secret,
        "grant_type": "refresh_token",
        "refresh_token": settings.tiktok_refresh_token,
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Cache-Control": "no-cache",
    }
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        r = await client.post(OAUTH_TOKEN_URL, data=payload, headers=headers)
        if r.status_code >= 400:
            log.error("tiktok_oauth_failed status=%s body=%s",
                      r.status_code, r.text[:500])
        r.raise_for_status()
        data = r.json()

    if "access_token" not in data:
        raise RuntimeError(f"TikTok no devolvio access_token: {data}")

    # Best-effort: persistir el nuevo refresh_token en /workspace/.tiktok_token
    # (NO en .env porque es read-only en el contenedor). El usuario lo puede
    # leer y refrescar el .env cuando quiera. Sin esto, despues de N rotaciones
    # tendria que re-OAuth, pero es flexible.
    new_refresh = data.get("refresh_token")
    if new_refresh and new_refresh != settings.tiktok_refresh_token:
        try:
            ws = Path(settings.workspace_dir) / ".tiktok_token"
            ws.write_text(new_refresh)
            # Actualizar en memoria para llamadas posteriores en este proceso
            settings.tiktok_refresh_token = new_refresh
            log.info("tiktok_refresh_token_rotated saved=%s", str(ws))
        except OSError as e:
            log.warning("tiktok_refresh_token_save_failed err=%s", e)

    return data


async def _get_access_token() -> str:
    data = await _refresh_access_token()
    return data["access_token"]


# ---------- Helpers ----------

def _normalize_for_tiktok(title: str, description: str, tags: list[str]) -> str:
    """TikTok no separa title/description: pone todo en 'title' (max 2200 chars).

    Anadimos hashtags al final para SEO. TikTok soporta hasta 100 hashtags
    pero limita el campo total a 2200 chars.
    """
    parts = [s for s in [title.strip(), description.strip()] if s]
    base = " — ".join(parts) if parts else "Horror Short"

    tags_lower = {t.lower().lstrip("#") for t in (tags or [])}
    must = ["fyp", "horror", "creepypasta", "miedo", "terror"]
    for m in must:
        if m not in tags_lower:
            tags_lower.add(m)

    hashtags_str = " ".join(f"#{t}" for t in list(tags_lower)[:30])
    full = f"{base}\n\n{hashtags_str}".strip()
    return full[:2200]


def _privacy_level_for_direct_post(policy_value: Any) -> str:
    """Garantiza que la privacidad sea uno de los valores aceptados."""
    valid = {
        "PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS",
        "FOLLOWER_OF_CREATOR", "SELF_ONLY",
    }
    val = policy_value if isinstance(policy_value, str) else None
    val = (val or settings.tiktok_privacy_level).upper()
    return val if val in valid else "SELF_ONLY"


# ---------- Init upload ----------

async def _init_inbox_upload(access_token: str, file_size: int) -> dict:
    """Inicia un upload tipo DIRECT_INBOX (no requiere audit)."""
    n_chunks = max(1, (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE)
    last_chunk = file_size - (n_chunks - 1) * CHUNK_SIZE
    body = {
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": file_size,
            "chunk_size": CHUNK_SIZE if n_chunks > 1 else file_size,
            "total_chunk_count": n_chunks,
        }
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
    }
    log.info("tiktok_init_inbox size=%s chunks=%s last=%s",
             file_size, n_chunks, last_chunk)
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        r = await client.post(INBOX_INIT_URL, json=body, headers=headers)
        if r.status_code >= 400:
            log.error("tiktok_init_inbox_failed status=%s body=%s",
                      r.status_code, r.text[:500])
        r.raise_for_status()
        return r.json()


async def _init_direct_post(
    access_token: str, file_size: int, post_info: dict,
) -> dict:
    """Inicia un upload tipo DIRECT_POST (requiere video.publish + audit)."""
    n_chunks = max(1, (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE)
    body = {
        "post_info": post_info,
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": file_size,
            "chunk_size": CHUNK_SIZE if n_chunks > 1 else file_size,
            "total_chunk_count": n_chunks,
        }
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
    }
    log.info("tiktok_init_direct size=%s chunks=%s privacy=%s",
             file_size, n_chunks, post_info.get("privacy_level"))
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        r = await client.post(POST_INIT_URL, json=body, headers=headers)
        if r.status_code >= 400:
            log.error("tiktok_init_direct_failed status=%s body=%s",
                      r.status_code, r.text[:500])
        r.raise_for_status()
        return r.json()


# ---------- Chunked upload to TikTok storage ----------

async def _put_chunks(upload_url: str, video_path: Path,
                      file_size: int) -> None:
    """Sube el video al upload_url devuelto por init, en chunks."""
    n_chunks = max(1, (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE)

    async with httpx.AsyncClient(timeout=UPLOAD_TIMEOUT) as client:
        with video_path.open("rb") as f:
            for i in range(n_chunks):
                start = i * CHUNK_SIZE
                end = min(start + CHUNK_SIZE, file_size) - 1
                length = end - start + 1
                f.seek(start)
                chunk = f.read(length)

                headers = {
                    "Content-Type": "video/mp4",
                    "Content-Length": str(length),
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                }
                log.info("tiktok_chunk_put i=%s/%s range=%s-%s/%s",
                         i + 1, n_chunks, start, end, file_size)

                # TikTok puede devolver 201 (intermedio) o 200 (final) o 308
                @retry(
                    stop=stop_after_attempt(3),
                    wait=wait_exponential(multiplier=1.5, min=1, max=8),
                    reraise=True,
                )
                async def _put_one():
                    rr = await client.put(upload_url, content=chunk,
                                          headers=headers)
                    if rr.status_code not in (200, 201, 202, 204, 308):
                        log.error("tiktok_chunk_failed status=%s body=%s",
                                  rr.status_code, rr.text[:300])
                        rr.raise_for_status()

                await _put_one()


# ---------- Status polling ----------

async def _fetch_status(access_token: str, publish_id: str) -> dict:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
    }
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        r = await client.post(
            STATUS_FETCH_URL,
            json={"publish_id": publish_id},
            headers=headers,
        )
        r.raise_for_status()
        return r.json()


async def _poll_until_done(access_token: str, publish_id: str,
                           max_wait_sec: int = 180) -> dict:
    """Polea status hasta que el video este procesado o fallido."""
    elapsed = 0
    interval = 5
    last_status = None
    while elapsed < max_wait_sec:
        await asyncio.sleep(interval)
        elapsed += interval
        try:
            data = await _fetch_status(access_token, publish_id)
        except Exception as e:
            log.warning("tiktok_status_fetch_error err=%s elapsed=%s", e, elapsed)
            continue
        st = (data.get("data") or {}).get("status")
        if st != last_status:
            log.info("tiktok_status publish_id=%s status=%s elapsed=%s",
                     publish_id, st, elapsed)
            last_status = st
        if st in ("PUBLISH_COMPLETE", "PUBLISH_OK", "SEND_TO_USER_INBOX",
                  "PROCESSING_DOWNLOAD"):
            # Aceptamos PROCESSING_DOWNLOAD como exito provisional para no
            # bloquear demasiado el render-service. TikTok seguira procesando.
            return data
        if st in ("FAILED", "PUBLISH_FAILED"):
            return data
    return {"data": {"status": last_status or "TIMEOUT", "publish_id": publish_id}}


# ---------- Public API ----------

async def upload_video(
    video_path: Path | str,
    title: str,
    description: str,
    tags: list[str],
    mode: Optional[str] = None,
    privacy_level: Optional[str] = None,
    poll: bool = True,
) -> dict:
    """Sube un video a TikTok via Content Posting API.

    Args:
        mode: 'inbox' (default, sin audit) o 'direct' (requiere video.publish).
        privacy_level: solo se aplica en modo 'direct'.
        poll: si True, espera hasta 180s a que TikTok confirme estado.

    Returns:
        dict con {publish_id, status, mode, raw} y los detalles de la API.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video no existe: {video_path}")

    file_size = video_path.stat().st_size
    if file_size < 1024:
        raise ValueError(f"Video demasiado pequeno: {file_size} bytes")

    mode = (mode or settings.tiktok_publish_mode or "inbox").lower()
    if mode not in ("inbox", "direct"):
        log.warning("tiktok_unknown_mode mode=%s falling_back_to_inbox", mode)
        mode = "inbox"

    access_token = await _get_access_token()
    caption = _normalize_for_tiktok(title, description, tags)

    if mode == "direct":
        post_info = {
            "title": caption,
            "privacy_level": _privacy_level_for_direct_post(privacy_level),
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
            "video_cover_timestamp_ms": 1000,
        }
        init_resp = await _init_direct_post(access_token, file_size, post_info)
    else:
        init_resp = await _init_inbox_upload(access_token, file_size)

    data = init_resp.get("data") or {}
    publish_id = data.get("publish_id")
    upload_url = data.get("upload_url")

    if not publish_id or not upload_url:
        raise RuntimeError(f"TikTok init invalid response: {init_resp}")

    log.info("tiktok_upload_start publish_id=%s mode=%s size=%s",
             publish_id, mode, file_size)

    await _put_chunks(upload_url, video_path, file_size)
    log.info("tiktok_upload_complete publish_id=%s mode=%s", publish_id, mode)

    status_payload: dict = {"data": {"status": "PROCESSING", "publish_id": publish_id}}
    if poll:
        status_payload = await _poll_until_done(access_token, publish_id)

    final_status = (status_payload.get("data") or {}).get("status", "UNKNOWN")
    return {
        "publish_id": publish_id,
        "status": final_status,
        "mode": mode,
        "init_response": init_resp,
        "status_response": status_payload,
        "caption": caption,
    }


async def query_creator_info() -> dict:
    """Sanity check: devuelve info del creador autenticado."""
    access_token = await _get_access_token()
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
    }
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        r = await client.post(CREATOR_INFO_URL, json={}, headers=headers)
        r.raise_for_status()
        return r.json()
