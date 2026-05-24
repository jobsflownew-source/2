"""Genera imagenes placeholder cuando las APIs de stock no devuelven resultados.

Asegura que el pipeline NUNCA falle por falta de imagenes. Cada placeholder
es 9:16 (1080x1920), cinematografico, con paleta dependiente del mood y
seed determinista para que la misma keyword siempre de la misma imagen.
"""
from __future__ import annotations

import hashlib
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

W, H = 1080, 1920

# (top_color_rgb, bottom_color_rgb, accent_for_halo_rgb)
MOOD_PALETTES = {
    "tension":   [(8, 10, 20),  (40, 5, 10),  (80, 20, 30)],
    "fear":      [(15, 5, 5),   (50, 10, 10), (90, 30, 30)],
    "despair":   [(10, 10, 15), (5, 5, 25),   (30, 20, 50)],
    "reveal":    [(20, 15, 5),  (40, 30, 5),  (90, 70, 20)],
    "aftermath": [(15, 10, 8),  (30, 20, 15), (70, 50, 35)],
    "default":   [(10, 10, 15), (30, 5, 5),   (60, 15, 15)],
}


def _seed_random(seed_text: str) -> random.Random:
    h = hashlib.md5((seed_text or "x").encode("utf-8")).hexdigest()
    return random.Random(int(h[:8], 16))


def generate_placeholder_image(
    out_path: str | Path,
    mood: str = "tension",
    seed_text: str = "",
) -> Path:
    """Genera una imagen JPEG cinematografica 9:16 y la escribe a out_path."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rng = _seed_random(seed_text or mood)
    palette = MOOD_PALETTES.get(mood, MOOD_PALETTES["default"])
    top, bottom, accent = palette

    # 1. Gradiente vertical no lineal
    img = Image.new("RGB", (W, H), top)
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = (y / H) ** 1.4
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        draw.rectangle([(0, y), (W, y + 1)], fill=(r, g, b))

    # 2. Halo de luz suave en posicion aleatoria
    halo = Image.new("RGB", (W, H), (0, 0, 0))
    hd = ImageDraw.Draw(halo)
    hx = rng.randint(W // 4, 3 * W // 4)
    hy = rng.randint(H // 4, H // 2)
    h_radius = rng.randint(280, 560)
    for radius in range(h_radius, 0, -8):
        alpha_t = (h_radius - radius) / h_radius
        color = tuple(int(accent[i] * alpha_t * 0.45) for i in range(3))
        hd.ellipse([hx - radius, hy - radius, hx + radius, hy + radius],
                   fill=color)
    halo = halo.filter(ImageFilter.GaussianBlur(radius=80))
    img = Image.blend(img, halo, alpha=0.30)

    # 3. Grano fotografico
    try:
        noise = Image.effect_noise((W, H), rng.randint(20, 40))
        noise = noise.convert("RGB").filter(ImageFilter.GaussianBlur(radius=1))
        img = Image.blend(img, noise, alpha=0.04)
    except Exception:
        pass

    # 4. Vignette (oscurecer bordes)
    vignette = Image.new("L", (W, H), 0)
    vd = ImageDraw.Draw(vignette)
    for i in range(60, 0, -1):
        alpha = int(255 * (i / 60))
        margin = int(i * 10)
        vd.rectangle([margin, margin, W - margin, H - margin], fill=alpha)
    vignette = vignette.filter(ImageFilter.GaussianBlur(radius=140))
    black = Image.new("RGB", (W, H), (0, 0, 0))
    img = Image.composite(img, black, vignette)

    # 5. Grano fino final
    try:
        final_noise = Image.effect_noise((W, H), 15).convert("RGB")
        img = Image.blend(img, final_noise, alpha=0.03)
    except Exception:
        pass

    img.save(out_path, "JPEG", quality=88)
    return out_path
