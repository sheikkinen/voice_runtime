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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from asyncio import Queue

logger = logging.getLogger(__name__)

VOICE_SERVER_PORT = int(os.getenv("VOICE_SERVER_PORT", "8080"))


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
    _ws_connected: threading.Event = field(
        default_factory=threading.Event, repr=False
    )
    _disconnected: threading.Event = field(
        default_factory=threading.Event, repr=False
    )
    _pending_marks: dict[str, threading.Event] = field(
        default_factory=dict, repr=False
    )
    _mark_queue: Queue[str] = field(
        default_factory=lambda: asyncio.Queue(), repr=False
    )

    # --- Optional audio monitoring ---
    _mixer: Any = field(default=None, repr=False)

    # --- Loop / lifecycle ---

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called by transport when event loop is available."""
        self._loop = loop

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        """Read-only access for sync→async bridging."""
        return self._loop

    # --- Queue API (thread-safe sync wrappers) ---

    def put_inbound(self, data: bytes | None) -> None:
        """Thread-safe enqueue to inbound (called by transport)."""
        if self._loop is None:
            return
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
            pass  # best-effort

    # --- Mark sync ---

    def send_mark_and_wait(self, mark_name: str, timeout: float = 10.0) -> None:
        """Queue a mark and block until transport echoes it back.

        Raises:
            TimeoutError: If mark not received within timeout (unless disconnected).
        """
        if self._loop is None:
            return

        event = threading.Event()
        self._pending_marks[mark_name] = event

        asyncio.run_coroutine_threadsafe(
            self._mark_queue.put(mark_name), self._loop
        )

        if not event.wait(timeout=timeout):
            del self._pending_marks[mark_name]
            if not self.is_disconnected:
                raise TimeoutError(f"Mark '{mark_name}' not received within {timeout}s")

        if mark_name in self._pending_marks:
            del self._pending_marks[mark_name]

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
        self._disconnected.clear()
        self._ws_connected.clear()
        self.stream_sid = None
        self._pending_marks.clear()
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
