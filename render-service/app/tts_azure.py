"""Azure Neural TTS (opcional, premium)."""
from __future__ import annotations

from pathlib import Path

import azure.cognitiveservices.speech as speechsdk

from .config import settings


def _build_ssml(text: str, voice: str, rate: str = "-8%", pitch: str = "-2st") -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<speak version="1.0" xml:lang="{settings.tts_language}">
  <voice name="{voice}">
    <prosody rate="{rate}" pitch="{pitch}">{text}</prosody>
  </voice>
</speak>"""


async def synthesize_azure(
    text: str,
    out_path: str | Path,
    voice: str | None = None,
) -> tuple[Path, list[dict], str]:
    if not settings.azure_speech_key:
        raise RuntimeError("AZURE_SPEECH_KEY no configurada")

    voice = voice or settings.tts_default_voice
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    speech_config = speechsdk.SpeechConfig(
        subscription=settings.azure_speech_key,
        region=settings.azure_speech_region,
    )
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio48Khz192KBitRateMonoMp3
    )
    audio_config = speechsdk.audio.AudioOutputConfig(filename=str(out_path))
    synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=speech_config, audio_config=audio_config
    )

    boundaries: list[dict] = []

    def _on_word(evt):
        boundaries.append({
            "offset_ms": evt.audio_offset / 10_000,
            "duration_ms": evt.duration.total_seconds() * 1000,
            "text": evt.text,
        })
    synthesizer.synthesis_word_boundary.connect(_on_word)

    ssml = _build_ssml(text, voice)
    result = synthesizer.speak_ssml_async(ssml).get()
    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        raise RuntimeError(f"Azure TTS fallo: {result.reason}")
    return out_path, boundaries, "azure_tts"
