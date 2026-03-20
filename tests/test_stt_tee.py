"""RED phase tests for NC-164: Multi-Provider STT with Secondary Logging.

Tests the SttTee adapter that fans audio to a primary + secondary STT,
relays set_speaking to both, and logs secondary transcripts without
routing them to FSM.
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

    def test_exposes_primary_transcript_queue(self):
        from voice_runtime.stt_tee import SttTee

        primary = MagicMock()
        primary._transcript_queue = asyncio.Queue()
        secondary = MagicMock()
        tee = SttTee(primary, secondary)
        assert tee._transcript_queue is primary._transcript_queue

    def test_proxies_direct_dispatch_to_primary(self):
        from voice_runtime.stt_tee import SttTee

        primary = MagicMock()
        secondary = MagicMock()
        tee = SttTee(primary, secondary)
        cb = MagicMock()
        tee._on_direct_dispatch = cb
        assert primary._on_direct_dispatch is cb

    def test_proxies_direct_transcribed_to_primary(self):
        from voice_runtime.stt_tee import SttTee

        primary = MagicMock()
        secondary = MagicMock()
        tee = SttTee(primary, secondary)
        cb = MagicMock()
        tee._on_direct_transcribed = cb
        assert primary._on_direct_transcribed is cb

    def test_does_not_wire_dispatch_to_secondary(self):
        from voice_runtime.stt_tee import SttTee

        primary = MagicMock()
        secondary = MagicMock()
        secondary._on_direct_dispatch = None
        tee = SttTee(primary, secondary)
        tee._on_direct_dispatch = MagicMock()
        # Secondary must remain None
        assert secondary._on_direct_dispatch is None


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


class TestSttTeeArmBargeIn:
    """arm_barge_in delegates to primary only."""

    def test_arms_primary_only(self):
        from voice_runtime.stt_tee import SttTee

        primary = MagicMock()
        evt = asyncio.Event()
        primary.arm_barge_in.return_value = evt
        secondary = MagicMock()
        tee = SttTee(primary, secondary)
        result = tee.arm_barge_in()
        assert result is evt
        primary.arm_barge_in.assert_called_once()
        secondary.arm_barge_in.assert_not_called()


class TestSttTeeNextTranscript:
    """next_transcript delegates to primary."""

    @pytest.mark.asyncio
    async def test_delegates_to_primary(self):
        from voice_runtime.stt_tee import SttTee

        primary = AsyncMock()
        primary.next_transcript.return_value = "hello"
        secondary = MagicMock()
        tee = SttTee(primary, secondary)
        result = await tee.next_transcript(timeout=5.0)
        assert result == "hello"
        primary.next_transcript.assert_awaited_once_with(timeout=5.0)


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


class TestSttTeeListeningProxy:
    """_listening property proxies to primary."""

    def test_get_listening(self):
        from voice_runtime.stt_tee import SttTee

        primary = MagicMock()
        primary._listening = True
        secondary = MagicMock()
        tee = SttTee(primary, secondary)
        assert tee._listening is True

    def test_set_listening(self):
        from voice_runtime.stt_tee import SttTee

        primary = MagicMock()
        secondary = MagicMock()
        tee = SttTee(primary, secondary)
        tee._listening = True
        assert primary._listening is True
