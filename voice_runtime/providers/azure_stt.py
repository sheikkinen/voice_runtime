"""Azure STT provider — persistent mode.

NC-161: Azure Speech-to-Text using push stream with native MULAW 8kHz.
NC-166: Simplified — fires on_committed callback, consumer decides routing.
NC-258: Canceled signal handling, reconnect with backoff, on_error callback.

AzurePersistentStt: continuous recognition for full call duration.
  - No streaming duration limit (unlike ElevenLabs/GCP)
  - Push stream maps directly to inbound queue
  - Echo discard window after TTS ends
  - on_committed callback for every committed transcript
  - on_error callback when STT dies and reconnect is exhausted
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import random
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
    NC-258: Connects canceled signal for death detection + auto-reconnect.
    """

    # NC-258 J-1: bound dead-air window
    _RECONNECT_BASE_DELAY_S = 1.0
    _RECONNECT_MAX_DELAY_S = 30.0
    _MAX_RECONNECT_ATTEMPTS = 3

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
        self._stopping = False  # NC-258 J-5: teardown race guard
        self._reconnect_attempt: int = 0  # NC-258: backoff counter
        self.on_committed: Callable[[str], None] | None = None
        self.on_recognizing: Callable[[str], None] | None = None
        self.on_error: Callable[[str], None] | None = None  # NC-258

    async def start(self, inbound_queue: asyncio.Queue[bytes | None]) -> None:
        """Open push stream, configure recognizer, begin feeding audio."""
        self._loop = asyncio.get_running_loop()
        self._stopping = False

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
        self._recognizer.recognizing.connect(self._on_recognizing)
        self._recognizer.canceled.connect(self._on_canceled)  # NC-258

        self._recognizer.start_continuous_recognition_async().get()

        self._feed_task = asyncio.create_task(
            self._feed_audio(inbound_queue), name="azure_stt_feed",
        )
        logger.info("AzurePersistentStt started (lang=%s)", self._language_code)

    async def stop(self) -> None:
        """Stop continuous recognition and close push stream."""
        self._stopping = True  # NC-258 J-5: prevent reconnect during teardown
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
                if frame_count % 100 == 0:  # NC-258 1c: frame count logging
                    logger.info("_feed_audio: %d frames fed (speaking=%s)", frame_count, self._speaking)
                self._push_stream.write(frame)
        except asyncio.CancelledError:
            logger.info("_feed_audio: cancelled after %d frames", frame_count)

    def _on_canceled(self, evt: Any) -> None:
        """Handle Azure canceled signal — reconnect on error, ignore on teardown.

        NC-258: Called from Azure SDK thread. Uses run_coroutine_threadsafe
        to schedule async reconnect on the event loop.
        NC-258 J-5: Skip if _stopping (normal teardown).
        """
        if self._stopping:
            logger.info("Azure STT canceled during stop — ignoring")
            return

        reason = evt.cancellation_details.reason
        error_details = evt.cancellation_details.error_details

        if reason == speechsdk.CancellationReason.Error:
            logger.error("Azure STT canceled: %s — %s", reason, error_details)
            if self._loop:
                asyncio.run_coroutine_threadsafe(
                    self._reconnect_after_error(), self._loop,
                )
        elif reason == speechsdk.CancellationReason.EndOfStream:
            logger.info("Azure STT: end of stream (expected)")
        else:
            logger.warning("Azure STT canceled: %s", reason)

    async def _reconnect_after_error(self) -> None:
        """Reconnect Azure STT with exponential backoff (NC-258).

        NC-258 J-1: Caps at _MAX_RECONNECT_ATTEMPTS, then fires on_error.
        """
        if self._reconnect_attempt >= self._MAX_RECONNECT_ATTEMPTS:
            logger.error(
                "Azure STT reconnect exhausted (%d attempts)",
                self._reconnect_attempt,
            )
            if self.on_error:
                self.on_error(
                    f"reconnect_exhausted_after_{self._reconnect_attempt}_attempts",
                )
            return

        delay = min(
            self._RECONNECT_BASE_DELAY_S * (2 ** self._reconnect_attempt),
            self._RECONNECT_MAX_DELAY_S,
        )
        delay *= 0.75 + random.random() * 0.5  # jitter ±25%
        logger.info(
            "Reconnecting Azure STT in %.1fs (attempt %d/%d)...",
            delay, self._reconnect_attempt + 1, self._MAX_RECONNECT_ATTEMPTS,
        )
        await asyncio.sleep(delay)

        try:
            await self._reconnect()
            logger.info(
                "Azure STT reconnected (attempt %d)", self._reconnect_attempt + 1,
            )
            self._reconnect_attempt = 0
        except Exception as exc:
            self._reconnect_attempt += 1
            logger.error(
                "Azure STT reconnect failed (attempt %d): %s",
                self._reconnect_attempt, exc,
            )
            await self._reconnect_after_error()  # recurse with incremented counter

    async def _reconnect(self) -> None:
        """Tear down and re-create recognizer + push stream (NC-258 J-2).

        Does NOT restart _feed_task — it keeps reading from the same
        inbound queue. Only the push stream sink changes.
        """
        # 1. Stop old recognizer
        if self._recognizer:
            with contextlib.suppress(Exception):
                self._recognizer.stop_continuous_recognition_async().get()
        # 2. Close old push stream
        if self._push_stream:
            with contextlib.suppress(Exception):
                self._push_stream.close()

        # 3. Re-create push stream + recognizer (same config)
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
        self._recognizer.recognizing.connect(self._on_recognizing)
        self._recognizer.canceled.connect(self._on_canceled)
        self._recognizer.start_continuous_recognition_async().get()

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

    def _on_recognizing(self, evt: Any) -> None:
        """Handle interim recognition — fire on_recognizing callback.

        NC-199: Signals that the user is still speaking.
        """
        text = evt.result.text
        if self._speaking:
            return
        cleaned = text.strip()
        if not cleaned:
            return
        if self.on_recognizing:
            self.on_recognizing(cleaned)
