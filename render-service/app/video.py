"""Video composition with MoviePy + FFmpeg.

Genera un Short 9:16 (1080x1920) a partir de:
- Lista de segmentos con (image_path, audio_path, text, duration_sec)
- Aplica Ken Burns (zoom suave) a cada imagen
- Mezcla audio narracion + (opcional) musica de fondo
- Subtitulos quemados con FFmpeg (ASS)
"""
from __future__ import annotations

import math
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image, ImageFilter

W, H = 1080, 1920
FPS = 30


@dataclass
class Segment:
    image_path: Path
    audio_path: Path
    text: str
    duration_sec: float


def _fit_to_vertical(src: Path, dst: Path) -> Path:
    """Recorta/escala imagen a 9:16 con relleno blur si hace falta."""
    img = Image.open(src).convert("RGB")
    target_ratio = W / H
    src_ratio = img.width / img.height

    if abs(src_ratio - target_ratio) < 0.02:
        out = img.resize((W, H), Image.LANCZOS)
    elif src_ratio < target_ratio:
        # mas estrecha: rellenamos con blur del propio fondo
        scale = W / img.width
        new_h = int(img.height * scale)
        fg = img.resize((W, new_h), Image.LANCZOS)
        bg = img.resize((W, H), Image.LANCZOS).filter(
            ImageFilter.GaussianBlur(radius=40)
        )
        offset_y = (H - new_h) // 2
        bg.paste(fg, (0, offset_y))
        out = bg
    else:
        # mas ancha: crop centrado al alto, escalado al ancho
        scale = H / img.height
        new_w = int(img.width * scale)
        scaled = img.resize((new_w, H), Image.LANCZOS)
        left = (new_w - W) // 2
        out = scaled.crop((left, 0, left + W, H))
    out.save(dst, "JPEG", quality=92)
    return dst


def _ken_burns_clip(image_path: Path, duration: float, out_path: Path,
                    zoom_start: float = 1.0, zoom_end: float = 1.12) -> Path:
    """Genera un clip MP4 con efecto Ken Burns usando FFmpeg zoompan."""
    frames = max(int(duration * FPS), 1)
    # zoompan necesita zoom entero/lineal; usamos expresion frame-based
    z_per_frame = (zoom_end - zoom_start) / frames
    zoom_expr = f"min(zoom+{z_per_frame:.6f},{zoom_end:.4f})"
    # Pequeno pan horizontal aleatorio segun hash del nombre
    pan_dir = 1 if hash(str(image_path)) % 2 == 0 else -1
    x_expr = f"iw/2-(iw/zoom/2)+{pan_dir}*on*0.3"
    y_expr = "ih/2-(ih/zoom/2)"

    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", str(image_path),
        "-t", f"{duration:.3f}",
        "-vf",
        f"scale={W*4}:{H*4}:force_original_aspect_ratio=increase,"
        f"crop={W*4}:{H*4},"
        f"zoompan=z='{zoom_expr}':d={frames}:x='{x_expr}':y='{y_expr}':"
        f"s={W}x{H}:fps={FPS}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
        "-r", str(FPS),
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def _concat_clips(clips: list[Path], out_path: Path) -> Path:
    """Concatena clips MP4 con xfade ligero."""
    if len(clips) == 1:
        cmd = ["ffmpeg", "-y", "-i", str(clips[0]),
               "-c", "copy", str(out_path)]
        subprocess.run(cmd, check=True, capture_output=True)
        return out_path

    # concat demuxer: rapido, sin re-encode
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for c in clips:
            f.write(f"file '{c.absolute()}'\n")
        list_file = f.name

    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
           "-i", list_file, "-c", "copy", str(out_path)]
    subprocess.run(cmd, check=True, capture_output=True)
    Path(list_file).unlink(missing_ok=True)
    return out_path


def _concat_audios(audios: list[Path], out_path: Path) -> Path:
    """Concatena los MP3 de narracion."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for a in audios:
            f.write(f"file '{a.absolute()}'\n")
        list_file = f.name
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
           "-i", list_file, "-c:a", "aac", "-b:a", "192k", str(out_path)]
    subprocess.run(cmd, check=True, capture_output=True)
    Path(list_file).unlink(missing_ok=True)
    return out_path


def _build_ass_subtitles(segments: list[Segment], out_path: Path) -> Path:
    """SRT/ASS simple, una linea grande por segmento, posicion media-baja."""
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, "
        "BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,DejaVu Sans,68,&H00FFFFFF,&H00000000,"
        "&H80000000,1,0,1,4,2,2,80,80,260,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
    )

    def fmt(t: float) -> str:
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = t % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    lines = []
    cursor = 0.0
    for seg in segments:
        # Texto en chunks de ~6 palabras para legibilidad en mobile
        words = seg.text.split()
        if not words:
            cursor += seg.duration_sec
            continue
        chunks: list[list[str]] = []
        for i in range(0, len(words), 6):
            chunks.append(words[i:i + 6])
        per_chunk = seg.duration_sec / len(chunks)
        for c in chunks:
            txt = " ".join(c).replace("\n", " ")
            start = cursor
            end = cursor + per_chunk
            lines.append(
                f"Dialogue: 0,{fmt(start)},{fmt(end)},Default,,0,0,0,,{txt}"
            )
            cursor = end

    out_path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def render_short(
    segments: list[Segment],
    work_dir: Path,
    output_path: Path,
    music_path: Optional[Path] = None,
    burn_subtitles: bool = True,
) -> Path:
    """Renderiza un Short 9:16 listo para subir a YouTube."""
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1. Normalizar imagenes a 9:16
    norm_images: list[Path] = []
    for i, seg in enumerate(segments):
        norm = work_dir / f"img_{i:02d}.jpg"
        _fit_to_vertical(seg.image_path, norm)
        norm_images.append(norm)

    # 2. Generar clips Ken Burns
    clips: list[Path] = []
    for i, (img, seg) in enumerate(zip(norm_images, segments)):
        clip = work_dir / f"clip_{i:02d}.mp4"
        _ken_burns_clip(img, seg.duration_sec, clip)
        clips.append(clip)

    # 3. Concatenar video y audio por separado
    video_only = work_dir / "video_only.mp4"
    _concat_clips(clips, video_only)

    audio_concat = work_dir / "narration.m4a"
    _concat_audios([s.audio_path for s in segments], audio_concat)

    # 4. Mezclar audio (con musica opcional)
    if music_path and music_path.exists():
        mixed = work_dir / "audio_mix.m4a"
        cmd = [
            "ffmpeg", "-y",
            "-i", str(audio_concat),
            "-stream_loop", "-1", "-i", str(music_path),
            "-filter_complex",
            "[1:a]volume=0.18[bg];[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2[a]",
            "-map", "[a]", "-c:a", "aac", "-b:a", "192k", str(mixed),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        final_audio = mixed
    else:
        final_audio = audio_concat

    # 5. Combinar video + audio + (opcional) subtitulos
    pre_sub = work_dir / "pre_sub.mp4"
    cmd = [
        "ffmpeg", "-y", "-i", str(video_only), "-i", str(final_audio),
        "-c:v", "copy", "-c:a", "aac", "-shortest", str(pre_sub),
    ]
    subprocess.run(cmd, check=True, capture_output=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if burn_subtitles:
        ass_path = work_dir / "subs.ass"
        _build_ass_subtitles(segments, ass_path)
        cmd = [
            "ffmpeg", "-y", "-i", str(pre_sub),
            "-vf", f"ass={ass_path}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(output_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
    else:
        # solo re-mux con faststart
        cmd = [
            "ffmpeg", "-y", "-i", str(pre_sub),
            "-c", "copy", "-movflags", "+faststart", str(output_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)

    return output_path


def get_duration_sec(media_path: Path) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", str(media_path)]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(out.stdout.strip())
