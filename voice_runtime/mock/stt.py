"""Mock STT provider for scripted testing.

NC-267: Feeds pre-scripted utterances via inject(). Cross-thread safe
using loop.call_soon_threadsafe for inject from sync callers.
Conforms to SttProvider protocol.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


class MockStt:
    """STT provider that yields scripted utterances via inject()."""

    def __init__(self, **kwargs: Any) -> None:
        self.on_committed: Callable[[str], None] | None = None
        self.on_recognizing: Callable[[str], None] | None = None
        self.on_error: Callable[[str], None] | None = None
        self._utterances: asyncio.Queue[str] = asyncio.Queue()
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._kwargs = kwargs

    def inject(self, text: str) -> None:
        """Enqueue a scripted utterance (thread-safe).

        Can be called from any thread. Uses call_soon_threadsafe when
        the event loop is running in another thread.
        """
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._utterances.put_nowait, text)
        else:
            self._utterances.put_nowait(text)

    def set_speaking(self, speaking: bool) -> None:
        """No-op — mock has no echo discard logic."""

    async def start(self, inbound_queue: asyncio.Queue[bytes | None]) -> None:
        """Start the mock STT consumer loop.

        Spawns a background task that waits for injected utterances and
        fires on_committed. The inbound_queue (raw audio) is ignored —
        transcripts come from inject() calls.

        Returns immediately (like ElevenLabs/Azure providers) so callers
        can await start() without blocking.
        """
        self._loop = asyncio.get_running_loop()
        self._running = True
        self._feed_task = asyncio.create_task(
            self._consume_loop(), name="mock_stt_consume"
        )
        logger.info("MockStt started")

    async def _consume_loop(self) -> None:
        """Background task: dispatch injected utterances to on_committed."""
        while self._running:
            try:
                text = await asyncio.wait_for(self._utterances.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            if self.on_committed:
                self.on_committed(text)
                logger.debug("MockStt committed: %s", text[:60])

    async def stop(self) -> None:
        """Stop the consumer loop."""
        self._running = False
        if hasattr(self, "_feed_task") and self._feed_task:
            self._feed_task.cancel()
