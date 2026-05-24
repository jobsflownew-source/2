"""Cliente de YouTube Data API v3 para subir Shorts.

Usa OAuth con refresh_token (sin browser interaction tras setup inicial).
El refresh_token se obtiene una vez con scripts/youtube_oauth.py y se mete
en el .env como YOUTUBE_REFRESH_TOKEN.

Cuotas YouTube Data API:
- Default: 10.000 units/dia por proyecto GCP
- videos.insert = 1.600 units por upload -> ~6 uploads/dia
- Ampliable rellenando formulario "Quota Extension" en Cloud Console
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from .config import settings

log = logging.getLogger(__name__)


SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def _get_credentials() -> Credentials:
    """Construye credenciales desde refresh_token + client_id + client_secret.

    Refresca el access_token automaticamente (caduca en 1h, refresh_token
    no caduca a menos que el usuario lo revoque o no se use en 6 meses).
    """
    if not settings.youtube_client_id:
        raise RuntimeError("YOUTUBE_CLIENT_ID no configurado")
    if not settings.youtube_client_secret:
        raise RuntimeError("YOUTUBE_CLIENT_SECRET no configurado")
    if not settings.youtube_refresh_token:
        raise RuntimeError(
            "YOUTUBE_REFRESH_TOKEN no configurado. "
            "Ejecuta scripts/youtube_oauth.py para obtenerlo."
        )

    creds = Credentials(
        token=None,
        refresh_token=settings.youtube_refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.youtube_client_id,
        client_secret=settings.youtube_client_secret,
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return creds


def _normalize_for_shorts(title: str, description: str, tags: list[str]) -> tuple[str, str, list[str]]:
    """Asegura que el video sea catalogado como Short por YouTube.

    Reglas:
    - Vertical 9:16 (lo cumple nuestro render)
    - Duracion <= 60s preferiblemente; YouTube acepta hasta 3 min como Short
    - Hashtag #Shorts en titulo o descripcion (lo forzamos en description)
    - Tags incluyen 'shorts'
    """
    title_clean = title.strip()[:100]

    desc = description.strip()
    if "#shorts" not in desc.lower():
        desc = f"{desc}\n\n#Shorts #Horror #Terror"
    desc = desc[:5000]

    tags_lower = {t.lower() for t in tags}
    final_tags = list(tags)
    for required in ("shorts", "horror", "terror"):
        if required not in tags_lower:
            final_tags.append(required)
    final_tags = final_tags[:30]
    return title_clean, desc, final_tags


def _upload_sync(
    video_path: Path,
    title: str,
    description: str,
    tags: list[str],
    privacy: str = "public",
    category_id: str = "24",
    made_for_kids: bool = False,
    thumbnail_path: Optional[Path] = None,
) -> dict:
    """Sube un video con resumable upload. BLOQUEANTE — usa await asyncio.to_thread.

    Si thumbnail_path se provee, tras el upload exitoso hace
    videos.thumbnails.set() para sustituir el thumbnail por defecto
    (primer frame). Coste: ~50 quota units adicionales (despreciable
    sobre los 1.600 del upload).
    """
    creds = _get_credentials()
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)

    title_n, desc_n, tags_n = _normalize_for_shorts(title, description, tags)

    body = {
        "snippet": {
            "title": title_n,
            "description": desc_n,
            "tags": tags_n,
            "categoryId": category_id,
            "defaultLanguage": "es",
            "defaultAudioLanguage": "es",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": made_for_kids,
            "embeddable": True,
            "publicStatsViewable": True,
        },
    }

    media = MediaFileUpload(
        str(video_path),
        chunksize=8 * 1024 * 1024,   # 8 MB
        resumable=True,
        mimetype="video/mp4",
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    last_progress = -1
    while response is None:
        try:
            status, response = request.next_chunk()
        except HttpError as e:
            # 5xx -> reintenta el chunk; 4xx -> error definitivo
            if e.resp.status in (500, 502, 503, 504):
                log.warning("yt_chunk_retry status=%s", e.resp.status)
                continue
            raise
        if status:
            pct = int(status.progress() * 100)
            if pct != last_progress and pct % 10 == 0:
                log.info("yt_upload_progress percent=%s", pct)
                last_progress = pct

    video_id = response.get("id")
    log.info("yt_upload_done video_id=%s", video_id)

    # Tras upload exitoso, sustituir el thumbnail si se provee
    if thumbnail_path and Path(thumbnail_path).exists() and video_id:
        try:
            thumb_media = MediaFileUpload(
                str(thumbnail_path),
                mimetype="image/jpeg",
                resumable=False,
            )
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=thumb_media,
            ).execute()
            log.info("yt_thumbnail_set video_id=%s thumb=%s",
                     video_id, thumbnail_path.name)
            response["custom_thumbnail_set"] = True
        except HttpError as e:
            # No fallar el upload por un thumbnail. Lo log y seguimos.
            log.warning("yt_thumbnail_set_failed video_id=%s err=%s",
                        video_id, e)
            response["custom_thumbnail_set"] = False
        except Exception as e:
            log.warning("yt_thumbnail_set_unexpected video_id=%s err=%s",
                        video_id, e)
            response["custom_thumbnail_set"] = False

    return response


async def upload_video(
    video_path: Path | str,
    title: str,
    description: str,
    tags: list[str],
    privacy: Optional[str] = None,
    category_id: Optional[str] = None,
    thumbnail_path: Optional[Path | str] = None,
) -> dict:
    """Wrapper async. Devuelve la respuesta de YouTube videos.insert.

    Si thumbnail_path se provee y existe, tras el upload sustituye el
    thumbnail por defecto via videos.thumbnails.set().
    """
    return await asyncio.to_thread(
        _upload_sync,
        Path(video_path),
        title,
        description,
        tags,
        privacy=privacy or settings.youtube_default_privacy,
        category_id=category_id or settings.youtube_default_category_id,
        thumbnail_path=Path(thumbnail_path) if thumbnail_path else None,
    )


async def list_recent_uploads(max_results: int = 5) -> list[dict]:
    """Lista los videos mas recientes del canal autenticado (sanity check)."""
    return await asyncio.to_thread(_list_recent_uploads_sync, max_results)


def _list_recent_uploads_sync(max_results: int) -> list[dict]:
    creds = _get_credentials()
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    # mine=true devuelve los canales del usuario autenticado
    channels = youtube.channels().list(part="contentDetails", mine=True).execute()
    items = channels.get("items") or []
    if not items:
        return []
    uploads_playlist = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    pl = youtube.playlistItems().list(
        part="snippet,status",
        playlistId=uploads_playlist,
        maxResults=max_results,
    ).execute()
    return pl.get("items") or []
