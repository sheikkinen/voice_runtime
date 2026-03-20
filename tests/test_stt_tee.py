"""RED phase tests for NC-164: Multi-Provider STT with Secondary Logging.

Tests the SttTee adapter that fans audio to a primary + secondary STT,
relays set_speaking to both, and proxies on_committed to primary.

NC-166: Simplified — removed barge-in, direct dispatch, transcript queue,
        listening proxies. Only on_committed proxy remains.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestSttTeeInit:
    """SttTee wraps primary + secondary with audio fan-out."""

    def test_creates_with_primary_and_secondary(self):
        from voice_runtime.stt_tee import SttTee

        primary = MagicMock()
        secondary = MagicMock()
        tee = SttTee(primary, secondary)
        assert tee.primary is primary
        assert tee.secondary is secondary

    def test_proxies_on_committed_to_primary(self):
        from voice_runtime.stt_tee import SttTee

        primary = MagicMock()
        secondary = MagicMock()
        tee = SttTee(primary, secondary)
        cb = MagicMock()
        tee.on_committed = cb
        assert primary.on_committed is cb

    def test_reads_on_committed_from_primary(self):
        from voice_runtime.stt_tee import SttTee

        primary = MagicMock()
        cb = MagicMock()
        primary.on_committed = cb
        secondary = MagicMock()
        tee = SttTee(primary, secondary)
        assert tee.on_committed is cb


class TestSttTeeSetSpeaking:
    """set_speaking relays to both providers (amendment 1)."""

    def test_relays_to_both(self):
        from voice_runtime.stt_tee import SttTee

        primary = MagicMock()
        secondary = MagicMock()
        tee = SttTee(primary, secondary)
        tee.set_speaking(True)
        primary.set_speaking.assert_called_once_with(True)
        secondary.set_speaking.assert_called_once_with(True)

    def test_secondary_error_does_not_propagate(self):
        from voice_runtime.stt_tee import SttTee

        primary = MagicMock()
        secondary = MagicMock()
        secondary.set_speaking.side_effect = RuntimeError("boom")
        tee = SttTee(primary, secondary)
        tee.set_speaking(False)  # must not raise
        primary.set_speaking.assert_called_once_with(False)


class TestSttTeeLifecycle:
    """start/stop manage both providers with audio fan-out."""

    @pytest.mark.asyncio
    async def test_start_creates_fanout_task(self):
        from voice_runtime.stt_tee import SttTee

        primary = AsyncMock()
        secondary = AsyncMock()
        tee = SttTee(primary, secondary)
        inbound = asyncio.Queue()
        await tee.start(inbound)

        # Both providers should have start called with their own queues
        assert primary.start.await_count == 1
        assert secondary.start.await_count == 1
        # The queues passed to primary and secondary must NOT be the original
        primary_q = primary.start.call_args[0][0]
        secondary_q = secondary.start.call_args[0][0]
        assert primary_q is not inbound
        assert secondary_q is not inbound
        assert primary_q is not secondary_q

        await tee.stop()

    @pytest.mark.asyncio
    async def test_fanout_distributes_frames(self):
        from voice_runtime.stt_tee import SttTee

        primary = AsyncMock()
        secondary = AsyncMock()

        tee = SttTee(primary, secondary)
        inbound = asyncio.Queue()
        await tee.start(inbound)

        # Feed frames through the source inbound queue
        await inbound.put(b"frame1")
        await inbound.put(b"frame2")
        await inbound.put(None)  # sentinel

        # Allow fan-out to process
        await asyncio.sleep(0.1)

        # Both provider queues should have received the frames
        primary_q = primary.start.call_args[0][0]
        secondary_q = secondary.start.call_args[0][0]

        primary_frames = []
        while not primary_q.empty():
            primary_frames.append(primary_q.get_nowait())
        secondary_frames = []
        while not secondary_q.empty():
            secondary_frames.append(secondary_q.get_nowait())

        assert primary_frames == [b"frame1", b"frame2", None]
        assert secondary_frames == [b"frame1", b"frame2", None]

        await tee.stop()

    @pytest.mark.asyncio
    async def test_stop_stops_both(self):
        from voice_runtime.stt_tee import SttTee

        primary = AsyncMock()
        secondary = AsyncMock()
        tee = SttTee(primary, secondary)
        inbound = asyncio.Queue()
        await tee.start(inbound)
        await tee.stop()
        primary.stop.assert_awaited_once()
        secondary.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_secondary_start_error_does_not_block_primary(self):
        from voice_runtime.stt_tee import SttTee

        primary = AsyncMock()
        secondary = AsyncMock()
        secondary.start.side_effect = RuntimeError("secondary init failed")
        tee = SttTee(primary, secondary)
        inbound = asyncio.Queue()
        await tee.start(inbound)  # must not raise
        primary.start.assert_awaited_once()
        await tee.stop()
