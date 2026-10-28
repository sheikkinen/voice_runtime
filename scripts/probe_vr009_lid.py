#!/usr/bin/env python3
"""VR-009 AC-08: live probe of Azure continuous language identification.

Synthesizes non-personal Finnish and Swedish sentences with Azure TTS
(8 kHz mu-law, the telephony format), feeds each through
``AzurePersistentStt`` in LID mode (fi-FI, sv-SE, en-GB) and the Finnish
clip once more in single-locale mode as the commit_lag_ms baseline.
Prints the ``Azure STT committed`` lines and the recognized text.

Usage:
    AZURE_SPEECH_KEY=... AZURE_SPEECH_REGION=swedencentral \
        python scripts/probe_vr009_lid.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys

import azure.cognitiveservices.speech as speechsdk

import voice_runtime
from voice_runtime.providers.azure_stt import AzurePersistentStt

LID = ("fi-FI", "sv-SE", "en-GB")
CLIPS = {
    "fi": (
        "fi-FI-NooraNeural",
        "Hei, haluaisin varata ajan lääkärille ensi viikolle.",
    ),
    "sv": (
        "sv-SE-SofieNeural",
        "Hej, jag skulle vilja boka en tid hos läkaren nästa vecka.",
    ),
}
FRAME = 160  # 20 ms of 8 kHz mu-law
MULAW_SILENCE = b"\xff" * FRAME


class _Lines(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if msg.startswith(("Azure STT committed", "AzurePersistentStt started")):
            self.lines.append(msg)


def synthesize(voice: str, text: str, key: str, region: str) -> bytes:
    config = speechsdk.SpeechConfig(subscription=key, region=region)
    config.speech_synthesis_voice_name = voice
    config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Raw8Khz8BitMonoMULaw
    )
    synth = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=None)
    result = synth.speak_text_async(text).get()
    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        raise RuntimeError(f"TTS failed for {voice}: {result.reason}")
    return bytes(result.audio_data)


async def recognize(
    audio: bytes, languages: tuple[str, ...], handler: _Lines
) -> list[str]:
    stt = AzurePersistentStt(languages=languages)
    texts: list[str] = []
    errors: list[str] = []
    stt.on_committed = texts.append
    stt.on_error = errors.append
    handler.lines.clear()
    inbound: asyncio.Queue[bytes | None] = asyncio.Queue()
    await stt.start(inbound)
    for i in range(0, len(audio), FRAME):
        inbound.put_nowait(audio[i : i + FRAME])
        await asyncio.sleep(0.02)
    for _ in range(150):  # 3 s trailing silence closes the phrase
        inbound.put_nowait(MULAW_SILENCE)
        await asyncio.sleep(0.02)
    await asyncio.sleep(2.0)
    await stt.stop()
    if errors:
        raise RuntimeError(f"STT error: {errors}")
    return texts


async def main() -> int:
    key = os.environ["AZURE_SPEECH_KEY"]
    region = os.environ["AZURE_SPEECH_REGION"]
    handler = _Lines()
    logging.getLogger("voice_runtime.providers.azure_stt").addHandler(handler)
    logging.getLogger("voice_runtime.providers.azure_stt").setLevel(logging.INFO)
    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    print(
        f"region={region} sdk={speechsdk.__version__} "
        f"voice_runtime={voice_runtime.__file__} git={sha} python={sys.version.split()[0]}"
    )
    runs = [("lid", "fi", LID), ("lid", "sv", LID), ("single", "fi", ())]
    for mode, clip, languages in runs:
        voice, text = CLIPS[clip]
        audio = synthesize(voice, text, key, region)
        print(
            f"\n[{mode} {clip}] clip: Azure TTS {voice} Raw8Khz8BitMonoMULaw "
            f"{len(audio)} bytes ({len(audio) / 8000:.2f} s) text={text!r}"
        )
        texts = await recognize(audio, languages, handler)
        for line in handler.lines:
            print(f"  log: {line}")
        print(f"  recognized: {texts!r}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
