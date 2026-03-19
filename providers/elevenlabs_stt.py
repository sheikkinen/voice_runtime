"""ElevenLabs STT provider — persistent and per-turn modes.

NC-152: Merged from ninchat_voice/services/persistent_stt.py (263 lines,
production-refined) and outcaller/nodes/stt.py (185 lines, simpler).

PersistentSttSession: one Scribe WebSocket per call lifetime.
  - Barge-in during TTS via partial transcripts
  - Echo discard window after TTS ends
  - Stability grace for premature commits
  - Optional direct dispatch callback for mid-LLM speech

PerTurnStt: new Scribe connection per listen() call.
  - Simpler, fewer features
  - Suitable for graph-driven consumers that don't need barge-in
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
STT_MODEL_ID = os.getenv("STT_MODEL_ID", "scribe_v2_realtime")
STT_LANGUAGE_CODE = os.getenv("STT_LANGUAGE_CODE", "fi")
BARGE_IN_MIN_TEXT_LEN = 2
ECHO_DISCARD_WINDOW_S = 0.4


class PersistentSttSession:
    """One ElevenLabs Scribe WebSocket per call lifetime.

    Modes (set_speaking()):
      listening (_speaking=False): committed_transcript → transcript_queue
      speaking  (_speaking=True):  partial_transcript with text → barge_in_event
    """

    def __init__(self, api_key: str | None = None, language_code: str | None = None) -> None:
        self._api_key = api_key or ELEVENLABS_API_KEY
        self._language_code = language_code or STT_LANGUAGE_CODE
        self._loop: asyncio.AbstractEventLoop | None = None
        self._speaking = False
        self._direct_sent = False
        self.__listening = False
        self._barge_in_event: asyncio.Event | None = None
        self._transcript_queue: asyncio.Queue[str] = asyncio.Queue()
        self._discard_until: float = 0.0
        self._time_limit_event: asyncio.Event = asyncio.Event()
        self._stt: Any | None = None
        self._feed_task: asyncio.Task[None] | None = None
        self._on_direct_dispatch: Any | None = None
        self._on_direct_transcribed: Any | None = None
        self._inbound_queue: asyncio.Queue[bytes | None] | None = None
        self._speaking_since: float = 0.0

    @property
    def _listening(self) -> bool:
        return self.__listening

    @_listening.setter
    def _listening(self, value: bool) -> None:
        if value:
            self._direct_sent = False
        self.__listening = value

    async def start(self, inbound_queue: asyncio.Queue[bytes | None]) -> None:
        """Open Scribe connection and begin feeding audio."""
        self._loop = asyncio.get_running_loop()
        self._inbound_queue = inbound_queue
        # Drain any stale sentinels/frames left from a previous call's cleanup
        # racing with session reset (e.g., abort_listen arriving after drain).
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

        self._stt.on("partial_transcript", self._on_partial)
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
            self._barge_in_event = None
            self._discard_until = time.monotonic() + ECHO_DISCARD_WINDOW_S
            # Reconnect if TTS ran for a long time — Scribe degrades
            if was_speaking and self._loop:
                spoke_for = time.monotonic() - self._speaking_since
                if spoke_for > self._RECONNECT_AFTER_SPEAKING_S:
                    logger.info(
                        "Scribe reconnect: TTS lasted %.1fs (threshold %.1fs)",
                        spoke_for, self._RECONNECT_AFTER_SPEAKING_S,
                    )
                    asyncio.run_coroutine_threadsafe(self._connect(), self._loop)

    def arm_barge_in(self) -> asyncio.Event:
        """Return asyncio.Event that fires on first meaningful partial transcript."""
        self._barge_in_event = asyncio.Event()
        return self._barge_in_event

    async def next_transcript(self, timeout: float = 30.0) -> str | None:
        """Await next committed transcript."""
        self._listening = True
        try:
            return await asyncio.wait_for(self._transcript_queue.get(), timeout=timeout)
        except TimeoutError:
            return None
        finally:
            self._listening = False

    async def _reconnect_after_error(self) -> None:
        """Reconnect Scribe after a fatal error (e.g. queue_overflow).

        Drains stale frames from the inbound queue before reconnecting
        to avoid a cascade overflow from buffered audio.
        """
        # Drain stale frames that accumulated during the dead socket period
        if self._inbound_queue:
            drained = 0
            while not self._inbound_queue.empty():
                item = self._inbound_queue.get_nowait()
                if item is None:
                    # Re-enqueue sentinel — it must not be lost
                    self._inbound_queue.put_nowait(None)
                    break
                drained += 1
            if drained:
                logger.info("Drained %d stale frames before reconnect", drained)

        try:
            logger.info("Reconnecting Scribe after fatal error...")
            await self._connect()
            logger.info("Scribe reconnected successfully after error")
        except Exception as exc:
            logger.error("Scribe reconnect failed: %s", exc)

    async def _feed_audio(self, inbound: asyncio.Queue[bytes | None]) -> None:
        """Feed inbound audio to Scribe WebSocket."""
        frame_count = 0
        try:
            while True:
                frame = await inbound.get()
                if frame is None:
                    logger.info("_feed_audio: sentinel received after %d frames", frame_count)
                    self._transcript_queue.put_nowait(None)
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
                    # Dead socket after queue_overflow — wait for reconnect
                    logger.debug("_feed_audio: send failed, waiting for reconnect")
                    await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            logger.info("_feed_audio: cancelled after %d frames", frame_count)

    def _on_partial(self, data: dict) -> None:
        text = data.get("text", "")
        if text.strip():
            logger.info("STT partial: speaking=%s len=%d text=%r", self._speaking, len(text), text[:60])
        if not self._speaking or len(text) <= BARGE_IN_MIN_TEXT_LEN:
            return
        logger.info("barge-in candidate: firing event (text=%r)", text[:60])
        if self._barge_in_event and not self._barge_in_event.is_set() and self._loop:
            self._loop.call_soon_threadsafe(self._barge_in_event.set)

    def _on_committed(self, data: dict) -> None:
        text = data.get("text", "")
        in_echo = time.monotonic() < self._discard_until
        logger.info(
            "STT committed: speaking=%s listening=%s echo_discard=%s text=%r",
            self._speaking, self._listening, in_echo, text[:80],
        )
        if self._speaking:
            return
        if in_echo:
            logger.info("discarding echo committed: %r", text)
            return
        cleaned = text.strip()
        if not cleaned:
            return

        if not self._listening and self._on_direct_dispatch and not self._direct_sent:
            try:
                self._on_direct_dispatch(cleaned)
                if self._on_direct_transcribed:
                    try:
                        self._on_direct_transcribed(cleaned)
                    except Exception as cb_exc:
                        logger.warning("direct transcribed callback failed: %s", cb_exc)
                self._direct_sent = True
                logger.info("direct transcribed send: %r", cleaned[:60])
                return
            except Exception as exc:
                logger.warning("direct transcribed send failed: %s", exc)

        self._transcript_queue.put_nowait(cleaned)

    def _on_time_limit(self, data: dict) -> None:
        logger.critical(
            "ElevenLabs session time limit exceeded — STT degraded. data=%s", data,
        )
        if self._loop:
            self._loop.call_soon_threadsafe(self._time_limit_event.set)

    # Fatal errors that kill the Scribe session and require reconnect
    _FATAL_ERRORS = frozenset({"queue_overflow", "resource_exhausted"})

    def _on_error(self, data: dict) -> None:
        msg_type = data.get("message_type", "")
        logger.error("STT error event: %s", data)

        if msg_type in self._FATAL_ERRORS and self._loop and self._inbound_queue:
            logger.warning(
                "STT fatal error (%s) — scheduling reconnect", msg_type,
            )
            asyncio.run_coroutine_threadsafe(self._reconnect_after_error(), self._loop)


class PerTurnStt:
    """Per-turn ElevenLabs Scribe STT — new connection per listen() call.

    Simpler than PersistentSttSession: no barge-in, no echo discard,
    no stability grace. Suitable for graph-driven consumers.
    """

    def __init__(self, api_key: str | None = None, language_code: str | None = None) -> None:
        self._api_key = api_key or ELEVENLABS_API_KEY
        self._language_code = language_code or STT_LANGUAGE_CODE

    def listen(self, session: Any, timeout: float = 30.0) -> str:
        """Listen for speech and return transcript.

        Runs an async STT pipeline on the session's event loop.

        Args:
            session: VoiceSession with inbound queue and event loop.
            timeout: Max seconds to wait for speech.

        Returns:
            Transcribed text, or empty string on timeout/disconnect.
        """
        loop = session.loop
        if loop is None:
            return ""

        future = asyncio.run_coroutine_threadsafe(
            self._run_stt(session, timeout), loop
        )
        try:
            return future.result(timeout=timeout + 5)
        except Exception as e:
            logger.error("PerTurnStt error: %s", e)
            return ""

    async def _run_stt(self, session: Any, timeout: float) -> str:
        """Run single-turn STT pipeline."""
        from elevenlabs import ElevenLabs
        from elevenlabs.realtime.scribe import AudioFormat, CommitStrategy

        client = ElevenLabs(api_key=self._api_key)
        options = {
            "model_id": STT_MODEL_ID,
            "audio_format": AudioFormat.ULAW_8000,
            "sample_rate": 8000,
            "commit_strategy": CommitStrategy.VAD,
            "min_speech_duration_ms": 300,
            "max_silence_duration_ms": 1500,
            "vad_threshold": 0.5,
            "language_code": self._language_code,
        }
        stt = await client.speech_to_text.realtime.connect(options)

        done = asyncio.Event()
        result_holder: list[str] = []

        def on_committed_transcript(data: dict) -> None:
            transcript = data.get("text", "")
            if transcript.strip():
                result_holder.append(transcript)
                done.set()

        stt.on("committed_transcript", on_committed_transcript)

        async def feed_audio() -> None:
            while not done.is_set():
                if session.is_disconnected:
                    done.set()
                    return
                try:
                    frame = await asyncio.wait_for(session.inbound.get(), timeout=0.5)
                except TimeoutError:
                    continue
                if frame is None:
                    done.set()
                    return
                audio_b64 = base64.b64encode(frame).decode("ascii")
                await stt.send({"audio_base_64": audio_b64})

        try:
            feed_task = asyncio.create_task(feed_audio())
            try:
                await asyncio.wait_for(done.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning("PerTurnStt: timed out after %ds", timeout)
            feed_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await feed_task
        finally:
            await stt.close()

        return result_holder[0] if result_holder else ""
