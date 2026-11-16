"""Google Cloud Speech-to-Text v2 provider: Chirp 3 streaming, native mulaw.

VR-013 §1: persistent streaming recognition over SpeechAsyncClient.
  - One owned session task, _run(), opens, rotates, reconnects and closes
    streams, so at most one stream is open (VR-012 R-3, D-2).
  - Audio leaves a provider-owned carry buffer in 800-byte requests at no
    more than 1x real time, also after a reconnect or rotation (R-1, D-1).
  - Rotation at the first final after 270 s, forced at 290 s (5 min limit).
  - Retry policy R-2: backoff 1, 2, 4 s; terminal errors end the session.
  - The commit log line carries language, lag and length, never the text.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable, Coroutine, Sequence
from dataclasses import dataclass, field
from typing import Any

from google.api_core import exceptions as gexc
from google.api_core.client_options import ClientOptions
from google.cloud.speech_v2 import SpeechAsyncClient
from google.cloud.speech_v2.types import cloud_speech

from voice_runtime.providers._google_config import SAMPLE_RATE_HZ, SttSettings, snake

logger = logging.getLogger(__name__)

# Time seams: tests replace these to drive a virtual clock.
_monotonic = time.monotonic
_sleep = asyncio.sleep

ECHO_DISCARD_WINDOW_S = 0.4
REQUEST_BYTES = 800  # 100 ms of 8 kHz mu-law
_DRAIN_TIMEOUT_S = 5.0
_ROTATE_AFTER_S = 270.0
_FORCED_ROTATE_S = 290.0
_SUCCESS_AFTER_S = 5.0
_RECONNECT_BASE_DELAY_S = 1.0
_RECONNECT_MAX_DELAY_S = 30.0
_MAX_REPLACEMENTS = 3
_UNEXPECTED_EOF = "UnexpectedEOF"

_SpeechEvent = cloud_speech.StreamingRecognizeResponse.SpeechEventType

_RETRYABLE: tuple[type[Exception], ...] = (
    gexc.ServiceUnavailable,
    gexc.DeadlineExceeded,
    gexc.InternalServerError,
    gexc.Aborted,
)
_TERMINAL: tuple[type[Exception], ...] = (
    gexc.PermissionDenied,
    gexc.Unauthenticated,
    gexc.InvalidArgument,
    gexc.NotFound,
    gexc.ResourceExhausted,
)


async def _wait_any(events: Sequence[asyncio.Event], timeout: float | None) -> bool:
    """Wait until any event is set or the timeout passes; True if one is set."""
    if any(e.is_set() for e in events):
        return True
    if timeout is not None and timeout <= 0:
        return False
    tasks: set[asyncio.Future[Any]] = {asyncio.ensure_future(e.wait()) for e in events}
    if timeout is not None:
        tasks.add(asyncio.ensure_future(_sleep(timeout)))
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
    return any(e.is_set() for e in events)


@dataclass
class _Stream:
    """Per-stream state shared by the request generator and the reader."""

    end_requests: asyncio.Event = field(default_factory=asyncio.Event)
    input_done: asyncio.Event = field(default_factory=asyncio.Event)
    rotate_due: asyncio.Event = field(default_factory=asyncio.Event)
    got_response: bool = False
    opened_at: float = 0.0

    def succeeded(self) -> bool:
        return self.got_response or _monotonic() - self.opened_at >= _SUCCESS_AFTER_S


@dataclass
class _Outcome:
    kind: str  # "end" | "rotate" | "retry" | "terminal"
    name: str = ""
    succeeded: bool = False


class GooglePersistentStt:
    """Google Speech-to-Text v2 (Chirp 3) provider for persistent mode."""

    def __init__(
        self,
        project: str | None = None,
        location: str | None = None,
        model: str | None = None,
        language_code: str | None = None,
        phrase_hints: Sequence[str] | None = None,
        endpointing: str | None = None,
    ) -> None:
        self._cfg = SttSettings.resolve(
            project, location, model, language_code, phrase_hints, endpointing
        )
        self._client: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stopping = False
        self._owned_futures: set[asyncio.Future[Any]] = set()  # VR-005
        self._feed_task: asyncio.Future[Any] | None = None
        self._speaking = False
        self._discard_until = 0.0
        self._carry = bytearray()
        self._data_evt = asyncio.Event()
        self._input_ended = False
        self._last_send_at: float | None = None
        self._last_send_s = 0.0
        self._end_at: float | None = None  # last SPEECH_ACTIVITY_END
        self._terminal_fired = False
        self.on_committed: Callable[[str], None] | None = None
        self.on_recognizing: Callable[[str], None] | None = None
        self.on_error: Callable[[str], None] | None = None

    # ── Lifecycle ────────────────────────────────────────────────────────

    def _schedule_owned(
        self, coro: Coroutine[Any, Any, None]
    ) -> asyncio.Future[Any] | None:
        """Schedule session work on the loop, retaining the handle (VR-005)."""
        if self._stopping or self._loop is None:
            coro.close()
            return None
        task = self._loop.create_task(coro)
        self._owned_futures.add(task)
        task.add_done_callback(self._owned_futures.discard)
        return task

    async def start(self, inbound_queue: asyncio.Queue[bytes | None]) -> None:
        """Create the client and start the feeder and the session owner."""
        if not self._cfg.project:
            raise ValueError(
                "GooglePersistentStt: no project; set GOOGLE_SPEECH_PROJECT "
                "or GOOGLE_CLOUD_PROJECT"
            )
        self._loop = asyncio.get_running_loop()
        self._stopping = False
        self._client = SpeechAsyncClient(
            client_options=ClientOptions(
                api_endpoint=f"{self._cfg.location}-speech.googleapis.com",
                quota_project_id=self._cfg.project,
            )
        )
        self._feed_task = self._schedule_owned(self._feed(inbound_queue))
        self._schedule_owned(self._run())
        logger.info(
            "GooglePersistentStt started (lang=%s model=%s location=%s)",
            self._cfg.language_code,
            self._cfg.model,
            self._cfg.location,
        )

    async def stop(self) -> None:
        """Stop every owned task and close the client transport (VR-005)."""
        self._stopping = True
        owned = list(self._owned_futures)
        for task in owned:
            task.cancel()
        if owned:
            await asyncio.gather(*owned, return_exceptions=True)
        if self._client is not None:
            try:
                await self._client.transport.close()
            except Exception as exc:  # teardown must finish; the fault is logged
                logger.warning("Google STT transport close failed: %r", exc)
        logger.info("GooglePersistentStt stopped")

    def set_speaking(self, speaking: bool) -> None:
        """Toggle TTS speaking state; echo window starts when speaking ends."""
        self._speaking = speaking
        if not speaking:
            self._discard_until = _monotonic() + ECHO_DISCARD_WINDOW_S

    # ── Audio input ──────────────────────────────────────────────────────

    async def _feed(self, inbound: asyncio.Queue[bytes | None]) -> None:
        """Move inbound frames into the carry buffer; mute while speaking."""
        while True:
            frame = await inbound.get()
            if frame is None:
                self._input_ended = True
                self._data_evt.set()
                return
            self._carry.extend(b"\xff" * len(frame) if self._speaking else frame)
            self._data_evt.set()

    def _build_config_request(self) -> cloud_speech.StreamingRecognizeRequest:
        """Build the first request of every stream (VR-013 §1 Config)."""
        return self._cfg.config_request()

    async def _requests(
        self, stream: _Stream
    ) -> AsyncIterator[cloud_speech.StreamingRecognizeRequest]:
        """Yield the config, then paced audio requests from the carry buffer.

        Bytes leave the carry buffer only at the yield, so anything not yet
        yielded survives the end of this stream, in order (R-1, step 3).
        """
        yield self._build_config_request()
        while True:
            if not await self._await_batch(stream):
                return
            size = min(REQUEST_BYTES, len(self._carry))
            if size == 0:
                stream.input_done.set()
                return
            if self._last_send_at is not None:
                ready_at = self._last_send_at + self._last_send_s
                if await _wait_any([stream.end_requests], ready_at - _monotonic()):
                    return
            size = min(REQUEST_BYTES, len(self._carry))
            chunk = bytes(self._carry[:size])
            del self._carry[:size]
            self._last_send_at = _monotonic()
            self._last_send_s = size / SAMPLE_RATE_HZ
            yield cloud_speech.StreamingRecognizeRequest(audio=chunk)

    async def _await_batch(self, stream: _Stream) -> bool:
        """Wait for a full batch or end of input; False when the stream ends."""
        while True:
            if stream.end_requests.is_set():
                return False
            if len(self._carry) >= REQUEST_BYTES or self._input_ended:
                return True
            self._data_evt.clear()
            await _wait_any([self._data_evt, stream.end_requests], None)

    # ── Session owner ────────────────────────────────────────────────────

    async def _run(self) -> None:
        """Open, rotate and reconnect streams until input ends or a fault."""
        failures = 0
        try:
            while not self._stopping:
                if failures:
                    delay = min(
                        _RECONNECT_BASE_DELAY_S * 2 ** (failures - 1),
                        _RECONNECT_MAX_DELAY_S,
                    )
                    logger.info(
                        "Google STT reconnect in %.0fs (replacement %d/%d)",
                        delay,
                        failures,
                        _MAX_REPLACEMENTS,
                    )
                    await _sleep(delay)
                outcome = await self._run_stream()
                if outcome.kind == "end":
                    return
                if outcome.kind == "rotate":
                    failures = 0
                    continue
                if outcome.kind == "terminal":
                    self._fire_error(f"google_stt_{snake(outcome.name)}")
                    return
                if outcome.succeeded:
                    failures = 0
                failures += 1
                logger.warning(
                    "Google STT stream failed: %s (failure %d)", outcome.name, failures
                )
                if failures > _MAX_REPLACEMENTS:
                    self._fire_error(f"reconnect_exhausted_{outcome.name}")
                    return
        finally:
            if self._feed_task is not None and not self._feed_task.done():
                self._feed_task.cancel()

    async def _run_stream(self) -> _Outcome:
        """Run one stream from open to rotation, end of input or failure."""
        stream = _Stream()
        self._end_at = None
        requests = self._requests(stream)
        try:
            call = await self._client.streaming_recognize(requests=requests)
        except Exception as exc:
            stream.end_requests.set()  # a lingering consumer takes no carry bytes
            return self._classify(exc, succeeded=False)
        stream.opened_at = _monotonic()
        logger.info("Google STT stream open")
        reader = asyncio.ensure_future(self._read(call, stream))
        try:
            trigger = await self._wait_trigger(stream, reader)
            if trigger == "reader":
                return self._reader_outcome(reader, stream)
            if trigger == "input_end":
                if not await self._drain(reader):
                    logger.info("Google STT final drain timed out; cancelling")
                    return _Outcome("end")
                outcome = self._reader_outcome(reader, stream)
                if outcome.kind == "terminal":
                    return outcome
                if outcome.kind == "retry":
                    logger.warning(
                        "Google STT error after end of input: %s", outcome.name
                    )
                return _Outcome("end")
            forced = trigger == "forced"
            age = _monotonic() - stream.opened_at
            stream.end_requests.set()
            drained = await self._drain(reader)
            if drained:
                outcome = self._reader_outcome(reader, stream)
                if outcome.kind in ("retry", "terminal"):
                    return outcome
            logger.log(
                logging.WARNING if forced else logging.INFO,
                "Google STT stream rotate age_s=%d forced=%s drained=%s",
                age,
                str(forced).lower(),
                str(drained).lower(),
            )
            return _Outcome("rotate")
        finally:
            stream.end_requests.set()
            if not reader.done():
                reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            call.cancel()
            self._end_at = None

    async def _wait_trigger(self, stream: _Stream, reader: asyncio.Future[Any]) -> str:
        """Wait for the reader to finish, a rotation or the end of input."""
        forced_in = stream.opened_at + _FORCED_ROTATE_S - _monotonic()
        waiters = {
            "rotate": asyncio.ensure_future(stream.rotate_due.wait()),
            "input_end": asyncio.ensure_future(stream.input_done.wait()),
            "forced": asyncio.ensure_future(_sleep(max(forced_in, 0.0))),
        }
        try:
            done, _ = await asyncio.wait(
                {reader, *waiters.values()}, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for waiter in waiters.values():
                waiter.cancel()
        if reader in done:
            return "reader"
        return next(name for name, waiter in waiters.items() if waiter in done)

    async def _drain(self, reader: asyncio.Future[Any]) -> bool:
        """Let the reader finish for at most _DRAIN_TIMEOUT_S; True if it did."""
        if reader.done():
            return True
        timer = asyncio.ensure_future(_sleep(_DRAIN_TIMEOUT_S))
        try:
            await asyncio.wait({reader, timer}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            timer.cancel()
        return reader.done()

    def _reader_outcome(self, reader: asyncio.Future[Any], stream: _Stream) -> _Outcome:
        exc = reader.exception()
        if exc is not None:
            return self._classify(exc, succeeded=stream.succeeded())
        if stream.input_done.is_set():
            return _Outcome("end")
        if stream.end_requests.is_set():
            return _Outcome("rotate")
        return _Outcome("retry", _UNEXPECTED_EOF, stream.succeeded())

    @staticmethod
    def _classify(exc: BaseException, succeeded: bool) -> _Outcome:
        name = type(exc).__name__
        if isinstance(exc, _RETRYABLE):
            return _Outcome("retry", name, succeeded)
        if not isinstance(exc, _TERMINAL):
            logger.error("Google STT unclassified error treated as terminal: %r", exc)
        return _Outcome("terminal", name)

    def _fire_error(self, reason: str) -> None:
        if self._terminal_fired or self._stopping:
            return
        self._terminal_fired = True
        logger.error("Google STT session ended: %s", reason)
        if self.on_error:
            self.on_error(reason)

    # ── Responses ────────────────────────────────────────────────────────

    async def _read(self, call: Any, stream: _Stream) -> None:
        async for response in call:
            stream.got_response = True
            self._handle_response(response, stream)

    def _handle_response(
        self, response: cloud_speech.StreamingRecognizeResponse, stream: _Stream
    ) -> None:
        event = response.speech_event_type
        if event == _SpeechEvent.SPEECH_ACTIVITY_BEGIN:
            if not self._speaking and self.on_recognizing:
                self.on_recognizing("")
        elif event == _SpeechEvent.SPEECH_ACTIVITY_END:
            self._end_at = _monotonic()
        for result in response.results:
            text = result.alternatives[0].transcript if result.alternatives else ""
            if result.is_final:
                self._on_final(text, stream)
            else:
                self._on_interim(text)

    def _on_interim(self, text: str) -> None:
        cleaned = text.strip()
        if not cleaned or self._speaking:
            return
        if self.on_recognizing:
            self.on_recognizing(cleaned)

    def _on_final(self, text: str, stream: _Stream) -> None:
        """Commit a final result; every final consumes the END timestamp."""
        end_at, self._end_at = self._end_at, None
        now = _monotonic()
        if now - stream.opened_at >= _ROTATE_AFTER_S:
            stream.rotate_due.set()
        if self._speaking:
            return
        cleaned = text.strip()
        if now < self._discard_until:
            logger.info("Google STT discarding echo committed chars=%d", len(cleaned))
            return
        if not cleaned:
            return
        lag_ms = -1 if end_at is None else round((now - end_at) * 1000)
        logger.info(
            "Google STT committed lang=%s commit_lag_ms=%d chars=%d",
            self._cfg.language_code,
            lag_ms,
            len(cleaned),
        )
        if self.on_committed:
            self.on_committed(cleaned)
