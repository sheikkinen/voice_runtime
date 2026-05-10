"""Azure TTS provider — native mulaw streaming.

NC-161: Azure Text-to-Speech with Raw8Khz8BitMonoMULaw output.
Event-based synthesis with barge-in support via stop_speaking_async().
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import azure.cognitiveservices.speech as speechsdk

if TYPE_CHECKING:
    from voice_runtime.session import VoiceSession

logger = logging.getLogger(__name__)

AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY", "")
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "westeurope")
AZURE_TTS_VOICE = os.getenv("AZURE_TTS_VOICE", "fi-FI-NooraNeural")


class AzureTTS:
    """Azure Text-to-Speech provider.

    Uses event-based synthesis with native MULAW 8kHz output.
    No ffmpeg needed.
    """

    def __init__(
        self,
        subscription_key: str | None = None,
        region: str | None = None,
        voice_name: str | None = None,
    ) -> None:
        self._subscription_key = subscription_key or os.getenv("AZURE_SPEECH_KEY", "")
        self._region = region or os.getenv("AZURE_SPEECH_REGION", "westeurope")
        self._voice_name = voice_name or os.getenv(
            "AZURE_TTS_VOICE", "fi-FI-NooraNeural"
        )
        self.on_error: Callable[[str], None] | None = None  # NC-260 Gap A

    def speak(
        self,
        text: str,
        session: VoiceSession,
        stop_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """Stream TTS audio to session outbound queue.

        Args:
            text: Text to speak.
            session: Active VoiceSession.
            stop_event: Threading event; set by barge-in to interrupt TTS.

        Returns:
            {"last_spoken": text} — may include {"call_disconnected": True}
            or {"interrupted": True} when stopped by stop_event.
        """
        if not text:
            return {"last_spoken": ""}

        if session.is_disconnected:
            logger.warning("Call disconnected — cannot speak")
            return {"last_spoken": "", "call_disconnected": True}

        logger.info("Speaking (Azure): %s", text[:80])
        t0 = time.time()

        speech_config = speechsdk.SpeechConfig(
            subscription=self._subscription_key,
            region=self._region,
        )
        speech_config.speech_synthesis_voice_name = self._voice_name
        speech_config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Raw8Khz8BitMonoMULaw
        )

        synthesizer = speechsdk.SpeechSynthesizer(
            speech_config=speech_config,
            audio_config=None,
        )

        interrupted = False

        def on_synthesizing(evt: Any) -> None:
            nonlocal interrupted
            if interrupted:
                return
            audio_data = evt.result.audio_data
            if not audio_data:
                return
            if stop_event and stop_event.is_set():
                interrupted = True
                return
            session.put_outbound_sync(audio_data)
            session.tap_agent(audio_data)

        synthesizer.synthesizing.connect(on_synthesizing)

        try:
            synthesizer.speak_text_async(text).get()
        except Exception as exc:
            logger.error("Azure TTS synthesis failed: %s", exc)
            if self.on_error:
                self.on_error(f"azure_synthesis_failed: {exc}")
            return {"last_spoken": text, "error": str(exc)}

        if interrupted:
            logger.info("Barge-in interrupt (Azure)")
            return {"last_spoken": text, "interrupted": True}

        logger.info("Spoke (Azure): %s (%.2fs)", text[:50], time.time() - t0)

        try:
            session.send_mark_and_wait("tts_complete", timeout=30.0)
        except TimeoutError:
            logger.warning("Mark timeout — audio may have been cut off")

        return {"last_spoken": text}
