"""Voice runtime provider protocols.

NC-165: SttProvider Protocol defines the consumer-facing contract for
all STT providers. Enforced by type checker (pyright/mypy), not at runtime.
NC-166: Simplified — routing decisions moved to consumers. Provider fires
on_committed callback, consumer decides action.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable


class SttProvider(Protocol):
    """Structural interface for speech-to-text providers.

    NC-166: Provider normalizes audio → text and fires on_committed
    for every committed utterance past echo discard. Consumer decides
    routing (queue, dispatch, ignore). Provider does not make policy
    decisions.
    """

    on_committed: Callable[[str], None] | None

    def set_speaking(self, speaking: bool) -> None: ...
    async def start(self, inbound_queue: asyncio.Queue[bytes | None]) -> None: ...
    async def stop(self) -> None: ...
