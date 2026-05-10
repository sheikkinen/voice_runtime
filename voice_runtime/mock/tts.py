"""Mock TTS provider for scripted testing.

NC-267: Records spoken text without audio synthesis.
NC-271: Adds send_mark_and_wait for FSM timing + on_spoken callback for text relay.
Conforms to TtsProvider protocol.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from voice_runtime.session import VoiceSession

logger = logging.getLogger(__name__)


class MockTts:
    """TTS provider that records calls without producing audio."""

    def __init__(self, on_spoken: Callable[[str], None] | None = None, **kwargs: Any) -> None:
        self.on_error: Callable[[str], None] | None = None
        self.on_spoken: Callable[[str], None] | None = on_spoken
        self.spoken: list[str] = []
        self._kwargs = kwargs

    def speak(
        self,
        text: str,
        session: VoiceSession,
        stop_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """Record text, fire on_spoken relay, and signal mark completion."""
        self.spoken.append(text)
        interrupted = stop_event.is_set() if stop_event else False
        if self.on_spoken:
            try:
                self.on_spoken(text)
            except Exception:
                logger.exception("on_spoken callback failed")
        # Skip mark wait when no event loop is wired (pure unit test context)
        if session._loop is not None:
            session.send_mark_and_wait("tts_complete", timeout=10.0)
        return {"last_spoken": text, "interrupted": interrupted}
