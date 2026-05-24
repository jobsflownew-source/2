"""Stock image search (Unsplash, Pixabay, Pexels) - todos free tier.

Las funciones piden un pool grande (per_page=15-20), aleatorizan el orden
y aceptan un set de URLs excluidas para evitar reutilizar imagenes que
ya aparecieron en shorts previos. Vease pipeline._produce_segment_image
y db.get_recent_image_urls / db.record_image_used.
"""
from __future__ import annotations

import random
from pathlib import Path
from typing import Iterable, Literal, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import settings

Provider = Literal["unsplash", "pixabay", "pexels"]

# Tamano del pool que pedimos a cada API. Mas alto = mas variedad y menos
# colisiones con las imagenes ya usadas, a costa de un poco mas de bandwidth.
DEFAULT_POOL_SIZE = 20


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=5))
async def search_unsplash(query: str, per_page: int = DEFAULT_POOL_SIZE,
                          page: int = 1) -> list[dict]:
    if not settings.unsplash_access_key:
        return []
    url = "https://api.unsplash.com/search/photos"
    params = {
        "query": query, "per_page": min(per_page, 30),
        "page": page, "orientation": "portrait",
    }
    headers = {"Authorization": f"Client-ID {settings.unsplash_access_key}"}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, params=params, headers=headers)
        r.raise_for_status()
        data = r.json()
    return [
        {
            "url": p["urls"]["regular"],
            "download": p["urls"]["full"],
            "author": p["user"]["name"],
            "source": "unsplash",
            "width": p["width"], "height": p["height"],
        }
        for p in data.get("results", [])
    ]


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=5))
async def search_pixabay(query: str, per_page: int = DEFAULT_POOL_SIZE,
                         page: int = 1) -> list[dict]:
    if not settings.pixabay_api_key:
        return []
    url = "https://pixabay.com/api/"
    params = {
        "key": settings.pixabay_api_key,
        "q": query,
        "image_type": "photo",
        "orientation": "vertical",
        "safesearch": "true",
        "per_page": max(min(per_page, 200), 3),
        "page": page,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    return [
        {
            "url": h["largeImageURL"],
            "download": h.get("fullHDURL") or h["largeImageURL"],
            "author": h.get("user"),
            "source": "pixabay",
            "width": h.get("imageWidth"), "height": h.get("imageHeight"),
        }
        for h in data.get("hits", [])
    ]


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=5))
async def search_pexels(query: str, per_page: int = DEFAULT_POOL_SIZE,
                        page: int = 1) -> list[dict]:
    if not settings.pexels_api_key:
        return []
    url = "https://api.pexels.com/v1/search"
    params = {
        "query": query, "per_page": min(per_page, 80),
        "page": page, "orientation": "portrait",
    }
    headers = {"Authorization": settings.pexels_api_key}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, params=params, headers=headers)
        r.raise_for_status()
        data = r.json()
    return [
        {
            "url": p["src"]["large2x"],
            "download": p["src"]["original"],
            "author": p["photographer"],
            "source": "pexels",
            "width": p.get("width"), "height": p.get("height"),
        }
        for p in data.get("photos", [])
    ]


def _is_portrait(r: dict) -> bool:
    h = r.get("height") or 0
    w = r.get("width") or 0
    return h > w


def _url_of(r: dict) -> str:
    return r.get("download") or r.get("url") or ""


def _filter_fresh(results: list[dict],
                  excluded_urls: Optional[set[str]]) -> list[dict]:
    """Devuelve solo las candidatas portrait y no excluidas."""
    if not results:
        return []
    excluded = excluded_urls or set()
    fresh = [r for r in results if _url_of(r) and _url_of(r) not in excluded]
    portrait_fresh = [r for r in fresh if _is_portrait(r)]
    return portrait_fresh if portrait_fresh else fresh


def _filter_and_pick(results: list[dict],
                     excluded_urls: Optional[set[str]]) -> Optional[dict]:
    """Filtra portrait + excluidas y elige una al azar.

    Preferencia:
      1. portrait y NO excluida (eleccion aleatoria)
      2. cualquiera NO excluida (eleccion aleatoria)
      3. None
    """
    fresh = _filter_fresh(results, excluded_urls)
    if not fresh:
        return None
    return random.choice(fresh)


async def find_best_image(
    keywords: list[str],
    excluded_urls: Optional[Iterable[str]] = None,
    pool_size: int = DEFAULT_POOL_SIZE,
) -> dict | None:
    """Busca una imagen para las keywords excluyendo URLs ya usadas.

    Estrategia:
      1. Para cada provider (Pixabay -> Unsplash -> Pexels), pide un pool
         de pool_size resultados con paginacion aleatoria (page 1-3).
      2. Filtra los que ya estan en excluded_urls.
      3. Elige uno aleatorio entre los portrait disponibles.
      4. Si todos los providers vienen vacios o todo el pool esta excluido,
         devuelve None y deja al caller usar el placeholder.
    """
    if not keywords:
        return None
    excluded_set = set(excluded_urls) if excluded_urls else set()
    query = " ".join(keywords[:3])

    for fn in (search_pixabay, search_unsplash, search_pexels):
        try:
            # Page aleatoria entre 1-3 para diversificar entre llamadas con
            # las mismas keywords. Pixabay tiene catalogos enormes.
            page = random.randint(1, 3)
            results = await fn(query, per_page=pool_size, page=page)
            if not results and page > 1:
                # Reintentar pagina 1 si la random no devolvio nada
                results = await fn(query, per_page=pool_size, page=1)
            picked = _filter_and_pick(results, excluded_set)
            if picked:
                return picked
        except Exception:
            continue
    return None


async def find_image_candidates(
    keywords: list[str],
    excluded_urls: Optional[Iterable[str]] = None,
    pool_size: int = DEFAULT_POOL_SIZE,
    top_k: int = 6,
) -> list[dict]:
    """Devuelve hasta top_k candidatas para que un selector externo elija.

    Se usa cuando hay AI-scoring activo (image_ai_selection=true en
    policy_params): pedimos varias y dejamos que GPT-4o-mini-vision
    decida cual encaja mejor con el segmento.

    Estrategia:
      - Para cada provider (Pixabay -> Unsplash -> Pexels), pide pool_size
        resultados con paginacion aleatoria.
      - Filtra excluded_urls y prefiere portrait.
      - Acumula entre providers hasta llegar a top_k.
      - Aleatoriza el orden final para no sesgar siempre al mismo provider.
    """
    if not keywords:
        return []
    excluded_set = set(excluded_urls) if excluded_urls else set()
    query = " ".join(keywords[:3])

    accumulated: list[dict] = []
    seen_urls: set[str] = set()

    for fn in (search_pixabay, search_unsplash, search_pexels):
        if len(accumulated) >= top_k:
            break
        try:
            page = random.randint(1, 3)
            results = await fn(query, per_page=pool_size, page=page)
            if not results and page > 1:
                results = await fn(query, per_page=pool_size, page=1)
            for r in _filter_fresh(results, excluded_set):
                u = _url_of(r)
                if u in seen_urls:
                    continue
                seen_urls.add(u)
                # Anotamos la query usada para que el selector tenga contexto
                r = dict(r)
                r["keywords_query"] = query
                accumulated.append(r)
                if len(accumulated) >= top_k * 2:
                    break
        except Exception:
            continue

    if not accumulated:
        return []

    random.shuffle(accumulated)
    return accumulated[:top_k]


async def download_image(image: dict, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r = await client.get(image["download"] or image["url"])
        r.raise_for_status()
        dest.write_bytes(r.content)
    return dest
