"""Azure Neural TTS (opcional, premium).

Cada voz tiene un perfil de prosody distinto para sonar atmosferica.
Las voces multilinguales tienden a ser mas planas y necesitan rate
mas lento + pitch mas grave para encajar en horror.
"""
from __future__ import annotations

from pathlib import Path

import azure.cognitiveservices.speech as speechsdk

from .config import settings


# Prosody overrides por voz: (rate, pitch) optimizados para misterio/terror
# Si la voz no esta listada usamos el default (-8%, -2st)
VOICE_PROSODY = {
    # Espana - masculinas
    "es-ES-AlvaroNeural":              ("-8%",  "-2st"),
    "es-ES-TristanMultilingualNeural": ("-12%", "-3st"),  # mas dramatica
    # Espana - femeninas
    "es-ES-IsidoraMultilingualNeural": ("-8%",  "-2st"),
    "es-ES-XimenaNeural":              ("-6%",  "-1st"),  # menos grave
    "es-ES-ArabellaMultilingualNeural":("-7%",  "-1st"),
    # Mexico
    "es-MX-JorgeNeural":               ("-7%",  "-2st"),
    "es-MX-DaliaNeural":               ("-6%",  "-1st"),
    # Colombia
    "es-CO-GonzaloNeural":             ("-8%",  "-2st"),
    "es-CO-SalomeNeural":              ("-6%",  "-1st"),
    # Argentina
    "es-AR-TomasNeural":               ("-8%",  "-2st"),
    "es-AR-ElenaNeural":               ("-7%",  "-1st"),
}


def _prosody_for(voice: str) -> tuple[str, str]:
    return VOICE_PROSODY.get(voice, ("-8%", "-2st"))


def _build_ssml(text: str, voice: str,
                rate: str | None = None, pitch: str | None = None) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if rate is None or pitch is None:
        auto_rate, auto_pitch = _prosody_for(voice)
        rate = rate or auto_rate
        pitch = pitch or auto_pitch
    # xml:lang debe coincidir con el locale de la voz para mejor calidad
    voice_lang = "-".join(voice.split("-")[:2]) if voice.count("-") >= 2 else settings.tts_language
    return f"""<speak version="1.0" xml:lang="{voice_lang}">
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
