"""Provider-agnostic voice call session coordinator.

Manages audio queues, mark synchronization, disconnect signaling,
and optional audio monitoring. Does not own the event loop — the
transport layer provides one and passes it via set_loop().

NC-152: Extracted from ninchat_voice/services/telephony.py and
outcaller/nodes/coordinator.py. Best-of-both merge.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from asyncio import Queue
    from collections.abc import Callable

    from voice_runtime.providers import SttProvider

logger = logging.getLogger(__name__)

VOICE_SERVER_PORT = int(os.getenv("VOICE_SERVER_PORT", "8080"))
FRAME_BYTES = 160  # 20ms @ 8kHz mulaw mono


class MissingStreamUrlError(Exception):
    """Raised when VOICE_STREAM_URL is not set."""

    def __init__(self) -> None:
        super().__init__(
            "Set VOICE_STREAM_URL to a public WebSocket URL pointing at "
            f"VOICE_SERVER_PORT (use ngrok for local dev). Current port: {VOICE_SERVER_PORT}"
        )


class CallNotAnsweredError(Exception):
    """Raised when WebSocket doesn't connect within timeout."""

    def __init__(self, timeout: float) -> None:
        super().__init__(f"WebSocket did not connect within {timeout}s timeout")


class CallHangupError(Exception):
    """Raised when call is hung up during listen operation."""

    def __init__(self) -> None:
        super().__init__("Call was hung up during listen operation")


@dataclass
class VoiceSession:
    """Provider-agnostic call session coordinator.

    Manages audio queues, mark synchronization, disconnect signaling,
    and optional audio monitoring. Does not own the event loop —
    the transport layer provides one and passes it via set_loop().
    """

    # --- Identity ---
    call_sid: str | None = None
    stream_sid: str | None = None
    caller_number: str | None = None

    # --- Audio queues ---
    inbound: Queue[bytes | None] = field(default_factory=lambda: asyncio.Queue())
    outbound: Queue[bytes] = field(default_factory=lambda: asyncio.Queue())

    # --- Event loop (set by transport, not owned) ---
    _loop: asyncio.AbstractEventLoop | None = field(default=None, repr=False)

    # --- Internal state ---
    _ws_connected: threading.Event = field(default_factory=threading.Event, repr=False)
    _disconnected: threading.Event = field(default_factory=threading.Event, repr=False)
    _pending_marks: dict[str, threading.Event] = field(default_factory=dict, repr=False)
    _mark_queue: Queue[str] = field(default_factory=lambda: asyncio.Queue(), repr=False)

    # --- Optional audio monitoring ---
    _mixer: Any = field(default=None, repr=False)

    # --- Transport intent (NC-154) ---
    _disconnect_requested: asyncio.Event | None = field(default=None, repr=False)
    _clear_queue: asyncio.Queue[str] | None = field(default=None, repr=False)
    stt: SttProvider | None = field(default=None, repr=False)
    stt_factory: Callable[[], SttProvider] | None = field(default=None, repr=False)
    stt_secondary_factory: Callable[[], SttProvider] | None = field(
        default=None, repr=False
    )
    on_stt_ready: Callable[[SttProvider], None] | None = field(default=None, repr=False)

    # --- Loop / lifecycle ---

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called by transport when event loop is available."""
        self._loop = loop
        # NC-170 Fix 3: init transport intent fields early to close
        # the pre-connection race (moved from signal_ws_connected).
        if self._disconnect_requested is None:
            self._disconnect_requested = asyncio.Event()
        if self._clear_queue is None:
            self._clear_queue = asyncio.Queue()

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        """Read-only access for sync→async bridging."""
        return self._loop

    # --- Queue API (thread-safe sync wrappers) ---

    _frame_size_warned: bool = False
    _loop_none_warned: bool = False

    def put_inbound(self, data: bytes | None) -> None:
        """Thread-safe enqueue to inbound (called by transport)."""
        if self._loop is None:
            if not self._loop_none_warned:
                logger.warning("put_inbound: _loop is None — audio frame dropped")
                self._loop_none_warned = True
            return
        # NC-170 Fix 4: log non-standard frame sizes (first occurrence only)
        if (
            data is not None
            and len(data) != FRAME_BYTES
            and len(data) > 0
            and not self._frame_size_warned
        ):
            logger.debug(
                "put_inbound: non-standard frame size %d (expected %d)",
                len(data),
                FRAME_BYTES,
            )
            self._frame_size_warned = True
        asyncio.run_coroutine_threadsafe(self.inbound.put(data), self._loop)

    def put_outbound_sync(self, data: bytes) -> None:
        """Thread-safe enqueue to outbound (called by TTS provider)."""
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self.outbound.put(data), self._loop)

    async def get_outbound(self) -> bytes:
        """Async dequeue from outbound (called by transport)."""
        return await self.outbound.get()

    def clear_inbound(self) -> None:
        """Drain stale audio from inbound queue."""
        if self._loop is None:
            return

        async def _drain() -> int:
            count = 0
            while not self.inbound.empty():
                try:
                    self.inbound.get_nowait()
                    count += 1
                except asyncio.QueueEmpty:
                    break
            return count

        future = asyncio.run_coroutine_threadsafe(_drain(), self._loop)
        try:
            cleared = future.result(timeout=1.0)
            if cleared > 0:
                logger.info("Cleared %d stale frames from inbound queue", cleared)
        except Exception:
            # NC-170 Fix 1: log instead of silently swallowing
            logger.debug("clear_inbound drain failed", exc_info=True)

    # --- Mark sync ---

    def send_mark_and_wait(self, mark_name: str, timeout: float = 10.0) -> None:
        """Queue a mark and block until transport echoes it back.

        NC-236: `mark_name` is a logical label (e.g. "tts_complete").
        Concurrent callers with the same label must not resolve each other's
        waits, so a unique suffix is appended before storing/enqueuing. The
        public API is unchanged; Twilio treats marks as opaque strings.

        Raises:
            TimeoutError: If mark not received within timeout (unless disconnected).
        """
        if self._loop is None:
            return

        # NC-236: disambiguate concurrent waiters sharing the same logical
        # name. Loop regenerates on any existing-key hit so the guarantee
        # is deterministic, not probabilistic.
        while True:
            unique = f"{mark_name}__{uuid.uuid4().hex[:8]}"
            if unique not in self._pending_marks:
                break

        event = threading.Event()
        self._pending_marks[unique] = event

        asyncio.run_coroutine_threadsafe(self._mark_queue.put(unique), self._loop)

        try:
            if not event.wait(timeout=timeout) and not self.is_disconnected:
                raise TimeoutError(
                    f"Mark '{mark_name}' (unique='{unique}') "
                    f"not received within {timeout}s"
                )
        finally:
            # Single deletion path — covers both success and timeout.
            self._pending_marks.pop(unique, None)

    def signal_mark_received(self, mark_name: str) -> None:
        """Called by transport when a mark echo arrives."""
        event = self._pending_marks.get(mark_name)
        if event is not None:
            logger.debug("Mark received: %s", mark_name)
            event.set()
        else:
            logger.warning("Received unknown mark: %s", mark_name)

    async def get_pending_mark(self) -> str:
        """Async dequeue next mark to send (called by transport)."""
        return await self._mark_queue.get()

    # --- Lifecycle ---

    def signal_ws_connected(self, stream_sid: str) -> None:
        """Called by transport when connection established."""
        self.stream_sid = stream_sid
        if self._loop is None:
            import contextlib

            with contextlib.suppress(RuntimeError):
                self._loop = asyncio.get_running_loop()
        # NC-154: lazy-init transport intent fields in the event loop context
        if self._disconnect_requested is None:
            self._disconnect_requested = asyncio.Event()
        if self._clear_queue is None:
            self._clear_queue = asyncio.Queue()
        self._ws_connected.set()
        logger.info("WebSocket connected: stream_sid=%s", stream_sid)

    def wait_for_ws_connect(self, timeout: float = 30.0) -> None:
        """Block until transport signals connection. Raises on timeout."""
        if not self._ws_connected.wait(timeout):
            raise CallNotAnsweredError(timeout)

    def signal_disconnected(self) -> None:
        """Called by transport on disconnect. Unblocks pending waits."""
        if not self._disconnected.is_set():
            logger.info("Call disconnected")
            self._disconnected.set()
            self.put_inbound(None)
            for event in self._pending_marks.values():
                event.set()

    @property
    def is_disconnected(self) -> bool:
        return self._disconnected.is_set()

    def reset(self) -> None:
        """Reset per-call state for session reuse (multi-call servers)."""
        # Unblock threads waiting on marks BEFORE clearing state.
        # Order matters: _disconnected must stay True while events fire
        # so stale commands see is_disconnected=True and abort.
        for event in self._pending_marks.values():
            event.set()
        self._pending_marks.clear()
        self._disconnected.clear()
        self._ws_connected.clear()
        self.stream_sid = None
        # NC-154: reset transport intent fields
        self._disconnect_requested = None
        self._clear_queue = None
        # Stop STT before clearing reference — prevents orphaned feed tasks.
        # stop() is async, so schedule it on the event loop if available.
        if self.stt is not None:
            if self._loop is not None and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(self.stt.stop(), self._loop)
            else:
                # Best-effort sync stop (cancel feed task at minimum)
                if hasattr(self.stt, "_feed_task") and self.stt._feed_task:
                    self.stt._feed_task.cancel()
        self.stt = None
        # Drain stale audio queues
        while True:
            try:
                self.inbound.get_nowait()
            except asyncio.QueueEmpty:
                break
        while True:
            try:
                self.outbound.get_nowait()
            except asyncio.QueueEmpty:
                break
        logger.info("VoiceSession reset for new call")

    # --- Audio monitoring (optional) ---

    def set_mixer(self, mixer: Any) -> None:
        """Attach an optional AudioMixer for local monitoring."""
        self._mixer = mixer

    def tap_caller(self, chunk: bytes) -> None:
        """Tee caller audio to mixer (no-op if no mixer)."""
        if self._mixer:
            self._mixer.write_caller(chunk)

    def tap_agent(self, chunk: bytes) -> None:
        """Tee agent audio to mixer (no-op if no mixer)."""
        if self._mixer:
            self._mixer.write_agent(chunk)

    # --- Transport intent (NC-154) ---

    def request_disconnect(self) -> None:
        """Consumer requests call termination. Thread-safe.

        Transport watches _disconnect_requested and closes in its own way
        (Twilio: websocket.close, SIP: BYE, etc.).
        """
        if self._loop is None or self._disconnect_requested is None:
            logger.debug("request_disconnect: prerequisites not met — skipping")
            return
        self._loop.call_soon_threadsafe(self._disconnect_requested.set)
        logger.info("Disconnect requested — transport will terminate call")

    def request_clear_buffer(self) -> None:
        """Consumer requests outbound buffer discard. Thread-safe.

        Transport watches _clear_queue and sends protocol-specific clear
        command (Twilio: 'clear' event, SIP: flush, etc.).
        """
        if self._loop is None or self._clear_queue is None or self.stream_sid is None:
            logger.debug("request_clear_buffer: prerequisites not met — skipping")
            return
        asyncio.run_coroutine_threadsafe(
            self._clear_queue.put(self.stream_sid), self._loop
        )
        logger.debug("Buffer clear requested for stream_sid=%s", self.stream_sid)
