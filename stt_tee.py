"""SttTee — fan-out adapter for primary + secondary STT providers.

NC-164: Runs two STT providers on the same audio stream. The primary
drives production (transcript queue, direct dispatch, barge-in). The
secondary receives the same frames for logging/comparison only.

Secondary errors never propagate to the caller.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

logger = logging.getLogger(__name__)


class SttTee:
    """Fan-out adapter: one inbound queue → two STT providers.

    The primary provider is the production path. The secondary
    receives the same audio frames but its transcripts are never
    routed to the FSM — they are handled by the secondary's own
    _on_committed (typically logging only).

    All interactive methods (arm_barge_in, next_transcript, direct
    dispatch callbacks) delegate to primary only. set_speaking()
    relays to both (secondary needs it for echo discard).
    """

    def __init__(self, primary: Any, secondary: Any) -> None:
        self.primary = primary
        self.secondary = secondary
        self._fanout_task: asyncio.Task[None] | None = None
        self._primary_queue: asyncio.Queue[bytes | None] | None = None
        self._secondary_queue: asyncio.Queue[bytes | None] | None = None

    # --- Proxy: transcript queue (read by stt.py _next_stable_transcript) ---

    @property
    def _transcript_queue(self) -> Any:
        return self.primary._transcript_queue

    # --- Proxy: _listening (set by next_transcript) ---

    @property
    def _listening(self) -> bool:
        return self.primary._listening

    @_listening.setter
    def _listening(self, value: bool) -> None:
        self.primary._listening = value

    # --- Proxy: direct dispatch (set by bridge_handlers) ---

    @property
    def _on_direct_dispatch(self) -> Any:
        return self.primary._on_direct_dispatch

    @_on_direct_dispatch.setter
    def _on_direct_dispatch(self, value: Any) -> None:
        self.primary._on_direct_dispatch = value

    @property
    def _on_direct_transcribed(self) -> Any:
        return self.primary._on_direct_transcribed

    @_on_direct_transcribed.setter
    def _on_direct_transcribed(self, value: Any) -> None:
        self.primary._on_direct_transcribed = value

    # --- Relay to both ---

    def set_speaking(self, speaking: bool) -> None:
        """Relay to both providers — secondary needs echo discard."""
        self.primary.set_speaking(speaking)
        try:
            self.secondary.set_speaking(speaking)
        except Exception:
            logger.warning("Secondary STT set_speaking failed", exc_info=True)

    # --- Primary-only methods ---

    def arm_barge_in(self) -> asyncio.Event:
        return self.primary.arm_barge_in()

    async def next_transcript(self, timeout: float = 30.0) -> str | None:
        return await self.primary.next_transcript(timeout=timeout)

    # --- Lifecycle ---

    async def start(self, inbound_queue: asyncio.Queue[bytes | None]) -> None:
        """Start both providers with fan-out queues."""
        self._primary_queue = asyncio.Queue()
        self._secondary_queue = asyncio.Queue()

        # Start primary (must succeed)
        await self.primary.start(self._primary_queue)

        # Start secondary (errors logged, never propagate)
        try:
            await self.secondary.start(self._secondary_queue)
        except Exception:
            logger.warning("Secondary STT start failed", exc_info=True)

        # Fan-out task: read from inbound, put to both queues
        self._fanout_task = asyncio.create_task(
            self._fanout(inbound_queue), name="stt_tee_fanout",
        )

    async def _fanout(self, source: asyncio.Queue[bytes | None]) -> None:
        """Read frames from source and distribute to both provider queues."""
        try:
            while True:
                frame = await source.get()
                self._primary_queue.put_nowait(frame)
                try:
                    self._secondary_queue.put_nowait(frame)
                except Exception:
                    pass  # secondary queue overflow — don't block primary
                if frame is None:
                    break
        except asyncio.CancelledError:
            # Propagate sentinel on cancel
            with contextlib.suppress(Exception):
                self._primary_queue.put_nowait(None)
            with contextlib.suppress(Exception):
                self._secondary_queue.put_nowait(None)

    async def stop(self) -> None:
        """Stop both providers and cancel fan-out."""
        if self._fanout_task:
            self._fanout_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._fanout_task
        with contextlib.suppress(Exception):
            await self.primary.stop()
        with contextlib.suppress(Exception):
            await self.secondary.stop()
