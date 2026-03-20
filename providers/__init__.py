"""Voice runtime provider protocols.

NC-165: SttProvider Protocol defines the consumer-facing contract for
all STT providers. Enforced by type checker (pyright/mypy), not at runtime.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable


class SttProvider(Protocol):
    """Structural interface for speech-to-text providers.

    Consumers (bridge_handlers.py, stt.py, twilio_ws.py) access these
    members on session.stt. All STT providers and SttTee must satisfy
    this Protocol.
    """

    _on_direct_dispatch: Callable[[str], None] | None
    _on_direct_transcribed: Callable[[str], None] | None

    def set_speaking(self, speaking: bool) -> None: ...
    def arm_barge_in(self) -> asyncio.Event: ...
    async def next_transcript(self, timeout: float = 30.0) -> str | None: ...
    async def start(self, inbound_queue: asyncio.Queue[bytes | None]) -> None: ...
    async def stop(self) -> None: ...
