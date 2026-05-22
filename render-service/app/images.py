"""Stock image search (Unsplash, Pixabay, Pexels) - todos free tier."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import settings

Provider = Literal["unsplash", "pixabay", "pexels"]


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=5))
async def search_unsplash(query: str, per_page: int = 5) -> list[dict]:
    if not settings.unsplash_access_key:
        return []
    url = "https://api.unsplash.com/search/photos"
    params = {"query": query, "per_page": per_page, "orientation": "portrait"}
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
async def search_pixabay(query: str, per_page: int = 5) -> list[dict]:
    if not settings.pixabay_api_key:
        return []
    url = "https://pixabay.com/api/"
    params = {
        "key": settings.pixabay_api_key,
        "q": query,
        "image_type": "photo",
        "orientation": "vertical",
        "safesearch": "true",
        "per_page": max(per_page, 3),
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
async def search_pexels(query: str, per_page: int = 5) -> list[dict]:
    if not settings.pexels_api_key:
        return []
    url = "https://api.pexels.com/v1/search"
    params = {"query": query, "per_page": per_page, "orientation": "portrait"}
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


async def find_best_image(keywords: list[str]) -> dict | None:
    """Busca por la primera keyword en todas las APIs y devuelve la mejor."""
    if not keywords:
        return None
    query = " ".join(keywords[:3])

    for fn in (search_pixabay, search_unsplash, search_pexels):
        try:
            results = await fn(query, per_page=3)
            if results:
                # Prefiere portrait real
                portraits = [r for r in results
                             if r.get("height", 0) > r.get("width", 0)]
                return portraits[0] if portraits else results[0]
        except Exception:
            continue
    return None


async def download_image(image: dict, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r = await client.get(image["download"] or image["url"])
        r.raise_for_status()
        dest.write_bytes(r.content)
    return dest
