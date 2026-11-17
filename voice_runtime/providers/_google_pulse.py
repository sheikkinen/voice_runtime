"""Speech-activity pulse for GooglePersistentStt (VR-015).

Chirp 3 sends no interim results, so between SPEECH_ACTIVITY_BEGIN and END a
consumer receives no event. SpeechPulse repeats a still-speaking signal on a
fixed cadence until it is stopped or reaches its cap, so a consumer's silence
timer does not expire mid-utterance. Times come from the running loop's clock,
the same clock asyncio.sleep uses.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

logger = logging.getLogger("voice_runtime.providers.google_stt")

Scheduler = Callable[[Coroutine[Any, Any, None]], "asyncio.Future[Any] | None"]


class SpeechPulse:
    """One periodic producer: start() replaces it, stop() cancels it."""

    def __init__(
        self,
        signal: Callable[[], None],
        schedule: Scheduler,
        interval_s: float,
        max_s: float,
    ) -> None:
        self._signal = signal
        self._schedule = schedule
        self._interval_s = interval_s
        self._max_s = max_s
        self._task: asyncio.Future[Any] | None = None

    def start(self) -> None:
        self.stop()
        self._task = self._schedule(self._run())

    def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + self._max_s
        tick = 0
        while True:
            tick += 1
            due = started + tick * self._interval_s
            if due >= deadline:
                await asyncio.sleep(max(deadline - loop.time(), 0.0))
                logger.warning(
                    "Google STT speech pulse cap reached after %.0fs", self._max_s
                )
                return
            await asyncio.sleep(max(due - loop.time(), 0.0))
            self._signal()
