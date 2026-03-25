"""Azure STT provider — persistent mode.

NC-161: Azure Speech-to-Text using push stream with native MULAW 8kHz.
NC-166: Simplified — fires on_committed callback, consumer decides routing.

AzurePersistentStt: continuous recognition for full call duration.
  - No streaming duration limit (unlike ElevenLabs/GCP)
  - Push stream maps directly to inbound queue
  - Echo discard window after TTS ends
  - on_committed callback for every committed transcript
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import Callable
from typing import Any

import azure.cognitiveservices.speech as speechsdk

logger = logging.getLogger(__name__)

AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY", "")
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "westeurope")
ECHO_DISCARD_WINDOW_S = 0.4


class AzurePersistentStt:
    """Azure Speech-to-Text provider for persistent mode.

    NC-166: Provider normalizes audio → text. Consumer decides routing
    via on_committed callback.
    """

    def __init__(
        self,
        subscription_key: str | None = None,
        region: str | None = None,
        language_code: str = "fi-FI",
        silence_timeout_ms: int = 1500,
    ) -> None:
        self._subscription_key = subscription_key or os.getenv("AZURE_SPEECH_KEY", "")
        self._region = region or os.getenv("AZURE_SPEECH_REGION", "westeurope")
        self._language_code = language_code
        self._silence_timeout_ms = silence_timeout_ms
        self._loop: asyncio.AbstractEventLoop | None = None
        self._speaking = False
        self._discard_until: float = 0.0
        self._push_stream: Any | None = None
        self._recognizer: Any | None = None
        self._feed_task: asyncio.Task[None] | None = None
        self.on_committed: Callable[[str], None] | None = None

    async def start(self, inbound_queue: asyncio.Queue[bytes | None]) -> None:
        """Open push stream, configure recognizer, begin feeding audio."""
        self._loop = asyncio.get_running_loop()

        # Audio format: MULAW 8kHz mono
        stream_format = speechsdk.audio.AudioStreamFormat(
            samples_per_second=8000,
            bits_per_sample=8,
            channels=1,
            wave_stream_format=speechsdk.AudioStreamWaveFormat.MULAW,
        )
        self._push_stream = speechsdk.audio.PushAudioInputStream(stream_format)
        audio_config = speechsdk.audio.AudioConfig(stream=self._push_stream)

        speech_config = speechsdk.SpeechConfig(
            subscription=self._subscription_key,
            region=self._region,
        )
        speech_config.speech_recognition_language = self._language_code
        speech_config.set_property(
            speechsdk.PropertyId.SpeechServiceConnection_EndSilenceTimeoutMs,
            str(self._silence_timeout_ms),
        )

        self._recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config,
            audio_config=audio_config,
        )
        self._recognizer.recognized.connect(self._on_committed)

        self._recognizer.start_continuous_recognition_async().get()

        self._feed_task = asyncio.create_task(
            self._feed_audio(inbound_queue), name="azure_stt_feed",
        )
        logger.info("AzurePersistentStt started (lang=%s)", self._language_code)

    async def stop(self) -> None:
        """Stop continuous recognition and close push stream."""
        if self._feed_task:
            self._feed_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._feed_task
        if self._recognizer:
            with contextlib.suppress(Exception):
                self._recognizer.stop_continuous_recognition_async().get()
        if self._push_stream:
            with contextlib.suppress(Exception):
                self._push_stream.close()
        logger.info("AzurePersistentStt stopped")

    def set_speaking(self, speaking: bool) -> None:
        """Toggle TTS speaking state for echo discard."""
        self._speaking = speaking
        if not speaking:
            self._discard_until = time.monotonic() + ECHO_DISCARD_WINDOW_S

    async def _feed_audio(self, inbound: asyncio.Queue[bytes | None]) -> None:
        """Feed inbound audio to Azure push stream."""
        frame_count = 0
        try:
            while True:
                frame = await inbound.get()
                if frame is None:
                    self._push_stream.close()
                    break
                frame_count += 1
                self._push_stream.write(frame)
        except asyncio.CancelledError:
            logger.info("_feed_audio: cancelled after %d frames", frame_count)

    def _on_committed(self, evt: Any) -> None:
        """Handle committed transcript — fire callback if valid.

        NC-166: No routing decisions. Echo discard is acoustic boundary
        concern; everything else is consumer policy.
        """
        text = evt.result.text
        if self._speaking:
            return
        if time.monotonic() < self._discard_until:
            logger.info("discarding echo committed: %r", text)
            return
        cleaned = text.strip()
        if not cleaned:
            return
        if self.on_committed:
            self.on_committed(cleaned)
