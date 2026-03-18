"""ElevenLabs TTS provider — streaming pipeline.

NC-152: Merged from ninchat_voice/services/tts.py and outcaller/nodes/tts.py.
Takes best of both: ninchat_voice's barge-in interrupt + outcaller's monitoring tap.

Pipeline: ElevenLabs API → ffmpeg subprocess (MP3 → mulaw 8kHz) → session outbound queue.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from projects.voice_runtime.session import VoiceSession

logger = logging.getLogger(__name__)

# Environment variables
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")


class ElevenLabsTTS:
    """ElevenLabs TTS provider.

    Streams text → ElevenLabs API → ffmpeg (MP3→mulaw) → session outbound queue.
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

    def speak(
        self,
        text: str,
        session: VoiceSession,
        stop_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """Stream TTS audio to session outbound queue via ffmpeg.

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

        proc = subprocess.Popen(
            [
                "ffmpeg",
                "-i", "pipe:0",
                "-f", "mulaw",
                "-ar", "8000",
                "-ac", "1",
                "pipe:1",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

        def _feed_mp3() -> None:
            audio_stream = client.text_to_speech.convert(
                voice_id=self._voice_id,
                model_id=self._model_id,
                text=text,
                output_format="mp3_22050_32",
            )
            for chunk in audio_stream:
                if proc.stdin:
                    proc.stdin.write(chunk)
            if proc.stdin:
                proc.stdin.close()

        feed_thread = threading.Thread(target=_feed_mp3, daemon=True)
        feed_thread.start()

        chunk_size = 160 if stop_event else 640
        while True:
            chunk = proc.stdout.read(chunk_size) if proc.stdout else b""
            if not chunk:
                break
            if stop_event and stop_event.is_set():
                logger.info("Barge-in interrupt: terminating ffmpeg")
                proc.terminate()
                return {"last_spoken": text, "interrupted": True}
            session.put_outbound_sync(chunk)
            session.tap_agent(chunk)

        feed_thread.join()
        proc.wait()

        logger.info("Spoke: %s (%.2fs)", text[:50], time.time() - t0)

        try:
            session.send_mark_and_wait("tts_complete", timeout=30.0)
        except TimeoutError:
            logger.warning("Mark timeout — audio may have been cut off")

        return {"last_spoken": text}
