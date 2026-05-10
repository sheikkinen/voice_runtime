"""Voice runtime provider protocols.

NC-165: SttProvider Protocol defines the consumer-facing contract for
all STT providers. Enforced by type checker (pyright/mypy), not at runtime.
NC-166: Simplified — routing decisions moved to consumers. Provider fires
on_committed callback, consumer decides action.
NC-258: on_error callback for STT death detection and recovery.
NC-260 Gap A: TtsProvider Protocol for TTS error detection.
"""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

    from voice_runtime.session import VoiceSession


class SttProvider(Protocol):
    """Structural interface for speech-to-text providers.

    NC-166: Provider normalizes audio → text and fires on_committed
    for every committed utterance past echo discard. Consumer decides
    routing (queue, dispatch, ignore). Provider does not make policy
    decisions.
    NC-258: on_error fires when STT encounters a fatal error and
    reconnect has been exhausted. Consumer forwards to FSM.
    """

    on_committed: Callable[[str], None] | None
    on_recognizing: Callable[[str], None] | None
    on_error: Callable[[str], None] | None

    def set_speaking(self, speaking: bool) -> None: ...
    async def start(self, inbound_queue: asyncio.Queue[bytes | None]) -> None: ...
    async def stop(self) -> None: ...


class TtsProvider(Protocol):
    """Structural interface for text-to-speech providers.

    NC-260 Gap A: TTS providers must expose on_error so synthesis failures
    are reported to the FSM instead of hanging in a speaking_* state.
    """

    on_error: Callable[[str], None] | None

    def speak(
        self,
        text: str,
        session: VoiceSession,
        stop_event: threading.Event | None = ...,
    ) -> dict[str, Any]: ...
