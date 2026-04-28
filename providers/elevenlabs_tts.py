"""ElevenLabs TTS provider — native mulaw streaming.

NC-152: Merged from ninchat_voice/services/tts.py and outcaller/nodes/tts.py.
Takes best of both: ninchat_voice's barge-in interrupt + outcaller's monitoring tap.

NC-159: Native ulaw_8000 output — eliminated ffmpeg subprocess pipeline.
Pipeline: ElevenLabs API (ulaw_8000) → session outbound queue.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from voice_runtime.session import VoiceSession

logger = logging.getLogger(__name__)

# Environment variables
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")


class ElevenLabsTTS:
    """ElevenLabs TTS provider.

    Streams text → ElevenLabs API (native ulaw_8000) → session outbound queue.
    Supports barge-in interrupt via stop_event and audio monitoring via session.tap_agent.
    """

    def __init__(
        self,
        api_key: str | None = None,
        voice_id: str | None = None,
        model_id: str | None = None,
    ) -> None:
        self._api_key = api_key or ELEVENLABS_API_KEY
        self._voice_id = voice_id or ELEVENLABS_VOICE_ID
        self._model_id = model_id or ELEVENLABS_MODEL
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
            stop_event: Threading event; set by barge-in to interrupt TTS mid-stream.

        Returns:
            {"last_spoken": text} — may include {"call_disconnected": True}
            or {"interrupted": True} when stopped by stop_event.
        """
        from elevenlabs import ElevenLabs

        if not text:
            return {"last_spoken": ""}

        if session.is_disconnected:
            logger.warning("Call disconnected — cannot speak")
            return {"last_spoken": "", "call_disconnected": True}

        logger.info("Speaking: %s", text[:80])
        t0 = time.time()
        client = ElevenLabs(api_key=self._api_key)

        try:
            audio_stream = client.text_to_speech.convert(
                voice_id=self._voice_id,
                model_id=self._model_id,
                text=text,
                output_format="ulaw_8000",
            )

            for chunk in audio_stream:
                if not chunk:
                    continue
                if stop_event and stop_event.is_set():
                    logger.info("Barge-in interrupt")
                    return {"last_spoken": text, "interrupted": True}
                session.put_outbound_sync(chunk)
                session.tap_agent(chunk)
        except Exception as exc:
            logger.error("ElevenLabs TTS failed: %s", exc)
            if self.on_error:
                self.on_error(f"elevenlabs_tts_failed: {exc}")
            return {"last_spoken": text, "error": str(exc)}

        logger.info("Spoke: %s (%.2fs)", text[:50], time.time() - t0)

        try:
            session.send_mark_and_wait("tts_complete", timeout=30.0)
        except TimeoutError:
            logger.warning("Mark timeout — audio may have been cut off")

        return {"last_spoken": text}
