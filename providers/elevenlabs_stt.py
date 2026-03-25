"""ElevenLabs STT provider — persistent mode.

NC-152: Merged from ninchat_voice and outcaller.
NC-166: Simplified — fires on_committed callback, consumer decides routing.
         PerTurnStt deleted (absorbed client functionality).

PersistentSttSession: one Scribe WebSocket per call lifetime.
  - Echo discard window after TTS ends
  - on_committed callback for every committed transcript
  - Reconnect after long TTS or fatal errors
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import os
import random
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
STT_MODEL_ID = os.getenv("STT_MODEL_ID", "scribe_v2_realtime")
STT_LANGUAGE_CODE = os.getenv("STT_LANGUAGE_CODE", "fi")
ECHO_DISCARD_WINDOW_S = 0.4


class PersistentSttSession:
    """One ElevenLabs Scribe WebSocket per call lifetime.

    NC-166: Provider normalizes audio → text. Consumer decides routing
    via on_committed callback.
    """

    def __init__(self, api_key: str | None = None, language_code: str | None = None) -> None:
        self._api_key = api_key or ELEVENLABS_API_KEY
        self._language_code = language_code or STT_LANGUAGE_CODE
        self._loop: asyncio.AbstractEventLoop | None = None
        self._speaking = False
        self._discard_until: float = 0.0
        self._time_limit_event: asyncio.Event = asyncio.Event()
        self._stt: Any | None = None
        self._feed_task: asyncio.Task[None] | None = None
        self._inbound_queue: asyncio.Queue[bytes | None] | None = None
        self._speaking_since: float = 0.0
        self.on_committed: Callable[[str], None] | None = None
        self._reconnect_attempt: int = 0  # NC-170 Fix 2: backoff counter

    async def start(self, inbound_queue: asyncio.Queue[bytes | None]) -> None:
        """Open Scribe connection and begin feeding audio."""
        self._loop = asyncio.get_running_loop()
        self._inbound_queue = inbound_queue
        while not inbound_queue.empty():
            try:
                inbound_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        await self._connect()
        self._feed_task = asyncio.create_task(
            self._feed_audio(inbound_queue), name="stt_feed"
        )
        logger.info("PersistentSttSession started")

    async def _connect(self) -> None:
        """Open (or re-open) the Scribe WebSocket."""
        from elevenlabs import ElevenLabs
        from elevenlabs.realtime.scribe import AudioFormat, CommitStrategy

        if self._stt is not None:
            with contextlib.suppress(Exception):
                await self._stt.close()

        client = ElevenLabs(api_key=self._api_key)
        options = {
            "model_id": STT_MODEL_ID,
            "audio_format": AudioFormat.ULAW_8000,
            "sample_rate": 8000,
            "commit_strategy": CommitStrategy.VAD,
            "vad_threshold": 0.5,
            "vad_silence_threshold_secs": 1.5,
            "min_speech_duration_ms": 300,
            "language_code": self._language_code,
        }
        self._stt = await client.speech_to_text.realtime.connect(options)

        self._stt.on("committed_transcript", self._on_committed)
        self._stt.on("session_time_limit_exceeded", self._on_time_limit)
        for ev in (
            "error", "auth_error", "quota_exceeded", "rate_limited",
            "queue_overflow", "resource_exhausted", "input_error",
            "transcriber_error", "chunk_size_exceeded",
        ):
            self._stt.on(ev, self._on_error)
        logger.info("Scribe WebSocket connected")

    async def stop(self) -> None:
        """Cancel feed task and close Scribe connection."""
        if self._feed_task:
            self._feed_task.cancel()
        if self._stt:
            with contextlib.suppress(Exception):
                await self._stt.close()
        logger.info("PersistentSttSession stopped")

    # Reconnect Scribe if speaking lasted longer than this (seconds)
    _RECONNECT_AFTER_SPEAKING_S = 10.0

    def set_speaking(self, speaking: bool) -> None:
        """Toggle speaking mode. Reconnects Scribe after long TTS periods."""
        logger.info("set_speaking: %s → %s", self._speaking, speaking)
        was_speaking = self._speaking
        self._speaking = speaking
        if speaking:
            self._speaking_since = time.monotonic()
        if not speaking:
            self._discard_until = time.monotonic() + ECHO_DISCARD_WINDOW_S
            if was_speaking and self._loop:
                spoke_for = time.monotonic() - self._speaking_since
                if spoke_for > self._RECONNECT_AFTER_SPEAKING_S:
                    logger.info(
                        "Scribe reconnect: TTS lasted %.1fs (threshold %.1fs)",
                        spoke_for, self._RECONNECT_AFTER_SPEAKING_S,
                    )
                    asyncio.run_coroutine_threadsafe(self._connect(), self._loop)

    # NC-170 Fix 2: exponential backoff constants
    _RECONNECT_BASE_DELAY_S = 1.0
    _RECONNECT_MAX_DELAY_S = 30.0

    async def _reconnect_after_error(self) -> None:
        """Reconnect Scribe after a fatal error with exponential backoff."""
        if self._inbound_queue:
            drained = 0
            while not self._inbound_queue.empty():
                item = self._inbound_queue.get_nowait()
                if item is None:
                    self._inbound_queue.put_nowait(None)
                    break
                drained += 1
            if drained:
                logger.info("Drained %d stale frames before reconnect", drained)

        # NC-170 Fix 2: exponential backoff with jitter
        delay = min(
            self._RECONNECT_BASE_DELAY_S * (2 ** self._reconnect_attempt),
            self._RECONNECT_MAX_DELAY_S,
        )
        delay *= 0.75 + random.random() * 0.5  # jitter ±25%

        logger.info(
            "Reconnecting Scribe in %.1fs (attempt %d)...",
            delay, self._reconnect_attempt + 1,
        )
        await asyncio.sleep(delay)

        try:
            await self._connect()
            logger.info("Scribe reconnected (attempt %d)", self._reconnect_attempt + 1)
            self._reconnect_attempt = 0
        except Exception as exc:
            self._reconnect_attempt += 1
            logger.error("Scribe reconnect failed (attempt %d): %s", self._reconnect_attempt, exc)

    async def _feed_audio(self, inbound: asyncio.Queue[bytes | None]) -> None:
        """Feed inbound audio to Scribe WebSocket."""
        frame_count = 0
        try:
            while True:
                frame = await inbound.get()
                if frame is None:
                    logger.info("_feed_audio: sentinel received after %d frames", frame_count)
                    break
                if self._time_limit_event.is_set():
                    logger.info("_feed_audio: time limit after %d frames", frame_count)
                    break
                frame_count += 1
                if frame_count % 100 == 0:
                    logger.info("_feed_audio: %d frames fed (speaking=%s)", frame_count, self._speaking)
                audio_b64 = base64.b64encode(frame).decode("ascii")
                try:
                    await self._stt.send({"audio_base_64": audio_b64})
                except Exception:
                    logger.debug("_feed_audio: send failed, waiting for reconnect")
                    await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            logger.info("_feed_audio: cancelled after %d frames", frame_count)

    def _on_committed(self, data: dict) -> None:
        """Handle committed transcript — fire callback if valid.

        NC-166: No routing decisions. Echo discard is acoustic boundary
        concern; everything else is consumer policy.
        """
        text = data.get("text", "")
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

    def _on_time_limit(self, data: dict) -> None:
        logger.critical(
            "ElevenLabs session time limit exceeded — STT degraded. data=%s", data,
        )
        if self._loop:
            self._loop.call_soon_threadsafe(self._time_limit_event.set)

    _FATAL_ERRORS = frozenset({"queue_overflow", "resource_exhausted"})

    def _on_error(self, data: dict) -> None:
        msg_type = data.get("message_type", "")
        logger.error("STT error event: %s", data)

        if msg_type in self._FATAL_ERRORS and self._loop and self._inbound_queue:
            logger.warning(
                "STT fatal error (%s) — scheduling reconnect", msg_type,
            )
            asyncio.run_coroutine_threadsafe(self._reconnect_after_error(), self._loop)
