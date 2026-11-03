"""Single-writer AudioHook send loop with byte-clock marks (VR-011).

Private to ``audiohook_server``. The clock is injected so the transport's
module-level ``_now``/``_sleep`` stay the single test seam.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import WebSocket

    from voice_runtime.session import VoiceSession

logger = logging.getLogger("voice_runtime.transports.audiohook_server")

PROTOCOL_VERSION = "2"
PACKET_BYTES = 1600  # 200 ms of PCMU; at most 5 binary messages per second
PACKET_INTERVAL_S = 0.2
PAD_BYTE = b"\xff"  # mu-law silence
BYTES_PER_SECOND = 8000
POLL_S = 0.02
_BARGE_IN = {"entities": [{"type": "barge_in", "data": {}}]}


@dataclass
class _Mark:
    name: str
    target: int
    anchor: float | None


class _Writer:
    """Single owner of every socket send (judgement R-1, R-3)."""

    def __init__(
        self,
        ws: WebSocket,
        session_id: str,
        clientseq: Callable[[], int],
        mark_latency_s: float,
        now: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]],
    ) -> None:
        self._now = now
        self._sleep = sleep
        self._ws = ws
        self._id = session_id
        self._clientseq = clientseq
        self._latency = mark_latency_s
        self._text: deque[tuple[str, dict, asyncio.Future]] = deque()
        self._seq = 0
        self._session: VoiceSession | None = None
        self._buffer = bytearray()
        self._marks: list[_Mark] = []
        self._next_send = 0.0
        self._last_audio_in: float | None = None
        self._failed = False
        self.sent_bytes = 0
        self.last_mark: tuple[str, float] | None = None

    def send(self, type_: str, parameters: dict | None = None) -> asyncio.Future:
        """Queue a text message; the future resolves True once written."""
        fut = asyncio.get_running_loop().create_future()
        if self._failed:
            fut.set_result(False)
        else:
            self._text.append((type_, parameters or {}, fut))
        return fut

    def bind(self, session: VoiceSession) -> None:
        self._session = session

    async def run(self) -> None:
        try:
            while True:
                await self._flush_text()
                if self._session is not None:
                    await self._service(self._session)
                await self._sleep(self._wait_s())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("AudioHook send failed: %s", exc)
            self._failed = True
            for _, _, fut in self._text:
                if not fut.done():
                    fut.set_result(False)
            self._text.clear()

    async def _write_text(self, type_: str, parameters: dict) -> None:
        self._seq += 1
        message = {
            "version": PROTOCOL_VERSION,
            "id": self._id,
            "type": type_,
            "seq": self._seq,
            "clientseq": self._clientseq(),
            "parameters": parameters,
        }
        await self._ws.send_text(json.dumps(message))

    async def _flush_text(self) -> None:
        while self._text:
            type_, parameters, fut = self._text[0]
            await self._write_text(type_, parameters)
            self._text.popleft()
            if not fut.done():
                fut.set_result(True)

    async def _service(self, session: VoiceSession) -> None:
        now = self._now()
        clears = session._clear_queue
        while clears is not None and not clears.empty():
            clears.get_nowait()
            await self._barge_in(session)
        self._drain(session, now)
        while not session._mark_queue.empty():
            name = session._mark_queue.get_nowait()
            if name not in session._pending_marks:
                continue
            target = self.sent_bytes + len(self._buffer)
            anchor = now if target == self.sent_bytes else None
            self._marks.append(_Mark(name, target, anchor))
        if self._buffer and now >= self._next_send:
            await self._send_packet(now)
        self._resolve_marks(session, self._now())

    def _drain(self, session: VoiceSession, now: float) -> None:
        received = False
        while not session.outbound.empty():
            self._buffer += session.outbound.get_nowait()
            received = True
        if received:
            self._last_audio_in = now

    def _should_pad(self, now: float) -> bool:
        if any(m.anchor is None for m in self._marks):
            return True
        return (
            self._last_audio_in is not None
            and now - self._last_audio_in >= PACKET_INTERVAL_S
        )

    async def _send_packet(self, now: float) -> None:
        if len(self._buffer) >= PACKET_BYTES:
            packet = bytes(self._buffer[:PACKET_BYTES])
            del self._buffer[:PACKET_BYTES]
        elif self._should_pad(now):
            packet = bytes(self._buffer).ljust(PACKET_BYTES, PAD_BYTE)
            self._buffer.clear()
        else:
            return
        await self._ws.send_bytes(packet)
        sent_at = self._now()
        self.sent_bytes += len(packet)
        self._next_send = sent_at + PACKET_INTERVAL_S
        for m in self._marks:
            if m.anchor is None and m.target <= self.sent_bytes:
                m.anchor = sent_at

    def _resolve_marks(self, session: VoiceSession, now: float) -> None:
        while self._marks:
            head = self._marks[0]
            if head.anchor is None or now < head.anchor + self._latency:
                return
            self._marks.pop(0)
            session.signal_mark_received(head.name)
            self.last_mark = (head.name, now)

    async def _barge_in(self, session: VoiceSession) -> None:
        self._buffer.clear()
        while not session.outbound.empty():
            session.outbound.get_nowait()
        while not session._mark_queue.empty():
            session._mark_queue.get_nowait()
        self._last_audio_in = None
        names = list(session._pending_marks)
        await self._write_text("event", _BARGE_IN)
        for name in names:
            session.signal_mark_received(name)
        self._marks = [m for m in self._marks if m.name not in names]
        logger.info("AudioHook barge_in sent; resolved %d pending marks", len(names))

    def _wait_s(self) -> float:
        if self._buffer:
            remaining = self._next_send - self._now()
            if 0 < remaining < POLL_S:
                return remaining
        return POLL_S

    def log_playback(self, kind: str, position: Any) -> None:
        """Spike evidence only: playback events never resolve marks."""
        if self.last_mark is None:
            name, since = None, "n/a"
        else:
            name, at = self.last_mark
            since = f"{self._now() - at:.3f}s"
        logger.info(
            "AudioHook %s position=%s byte_clock=%.3fs last_mark=%s since_mark=%s",
            kind,
            position,
            self.sent_bytes / BYTES_PER_SECOND,
            name,
            since,
        )
