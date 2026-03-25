"""SttTee — fan-out adapter for primary + secondary STT providers.

NC-164: Runs two STT providers on the same audio stream. The primary
drives production (on_committed callback). The secondary receives
the same frames for logging/comparison only.

NC-166: Simplified — on_committed proxied to primary only.
Secondary errors never propagate to the caller.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class SttTee:
    """Fan-out adapter: one inbound queue → two STT providers.

    The primary provider is the production path. The secondary
    receives the same audio frames but its transcripts are never
    routed to the FSM — they are handled by the secondary's own
    on_committed (typically logging only).

    on_committed and set_speaking() delegate to primary.
    set_speaking() relays to both (secondary needs it for echo discard).
    """

    def __init__(self, primary: Any, secondary: Any) -> None:
        self.primary = primary
        self.secondary = secondary
        self._fanout_task: asyncio.Task[None] | None = None
        self._primary_queue: asyncio.Queue[bytes | None] | None = None
        self._secondary_queue: asyncio.Queue[bytes | None] | None = None
        self._secondary_drops: int = 0  # NC-170 Fix 1: drop counter
        logger.info(
            "SttTee created: primary=%s, secondary=%s",
            type(primary).__name__,
            type(secondary).__name__,
        )

    # --- Proxy: on_committed (primary only) ---

    @property
    def on_committed(self) -> Callable[[str], None] | None:
        return self.primary.on_committed

    @on_committed.setter
    def on_committed(self, value: Callable[[str], None] | None) -> None:
        self.primary.on_committed = value

    # --- Relay to both ---

    def set_speaking(self, speaking: bool) -> None:
        """Relay to both providers — secondary needs echo discard."""
        self.primary.set_speaking(speaking)
        try:
            self.secondary.set_speaking(speaking)
        except Exception:
            logger.warning("Secondary STT set_speaking failed", exc_info=True)

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
                    # NC-170 Fix 1: count drops, log periodically
                    self._secondary_drops += 1
                    if self._secondary_drops % 500 == 1:
                        logger.warning(
                            "Secondary STT queue overflow: %d frames dropped",
                            self._secondary_drops,
                        )
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
        if self._secondary_drops:
            logger.info("SttTee stopped: %d secondary frames dropped total", self._secondary_drops)
        self._secondary_drops = 0
