"""Mock TTS provider for scripted testing.

NC-267: Records spoken text without audio synthesis.
Conforms to TtsProvider protocol.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from voice_runtime.session import VoiceSession


class MockTts:
    """TTS provider that records calls without producing audio."""

    def __init__(self, **kwargs: Any) -> None:
        self.on_error: Callable[[str], None] | None = None
        self.spoken: list[str] = []
        self._kwargs = kwargs

    def speak(
        self,
        text: str,
        session: VoiceSession,
        stop_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """Record text and return success without audio synthesis."""
        self.spoken.append(text)
        interrupted = stop_event.is_set() if stop_event else False
        return {"last_spoken": text, "interrupted": interrupted}
