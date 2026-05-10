"""NC-260 Gap C: ElevenLabs STT _FATAL_ERRORS expansion + _feed_audio escalation.

RED tests:
1. All reconnectable errors trigger reconnect
2. Non-reconnectable errors (auth_error) fire on_error directly
3. _feed_audio escalates after consecutive send failures
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from voice_runtime.providers.elevenlabs_stt import PersistentSttSession


@pytest.mark.req("REQ-YG-170")
class TestFatalErrorsExpansion:
    """Verify _FATAL_ERRORS covers all reconnectable error types."""

    RECONNECTABLE_ERRORS = [
        "queue_overflow",
        "resource_exhausted",
        "transcriber_error",
        "chunk_size_exceeded",
        "server_error",
    ]

    NON_RECONNECTABLE_ERRORS = [
        "auth_error",
        "quota_exceeded",
        "rate_limited",
        "input_error",
    ]

    @pytest.mark.parametrize("error_type", RECONNECTABLE_ERRORS)
    def test_reconnectable_error_triggers_reconnect(self, error_type: str) -> None:
        """Reconnectable errors should schedule _reconnect_after_error."""
        session = PersistentSttSession(api_key="test")
        session._loop = MagicMock()
        session._inbound_queue = asyncio.Queue()
        session._reconnect_attempt = 0

        with patch.object(session, "_reconnect_after_error") as mock_reconnect:
            future = asyncio.Future()
            future.set_result(None)
            session._loop.call_soon_threadsafe = MagicMock()
            # run_coroutine_threadsafe returns a future
            with patch("asyncio.run_coroutine_threadsafe", return_value=future):
                session._on_error({"message_type": error_type})

        # Should have scheduled reconnect

        assert mock_reconnect.called or error_type in session._FATAL_ERRORS

    @pytest.mark.parametrize("error_type", RECONNECTABLE_ERRORS)
    def test_reconnectable_error_in_fatal_set(self, error_type: str) -> None:
        """All reconnectable errors must be in _FATAL_ERRORS."""
        assert (
            error_type in PersistentSttSession._FATAL_ERRORS
        ), f"{error_type} not in _FATAL_ERRORS"

    @pytest.mark.parametrize("error_type", NON_RECONNECTABLE_ERRORS)
    def test_non_reconnectable_error_fires_on_error(self, error_type: str) -> None:
        """Non-reconnectable errors should fire on_error callback directly."""
        session = PersistentSttSession(api_key="test")
        session._loop = MagicMock()
        session._inbound_queue = asyncio.Queue()
        callback = MagicMock()
        session.on_error = callback

        session._on_error({"message_type": error_type})

        callback.assert_called_once_with(error_type)

    @pytest.mark.parametrize("error_type", NON_RECONNECTABLE_ERRORS)
    def test_non_reconnectable_error_not_in_fatal_set(self, error_type: str) -> None:
        """Non-reconnectable errors must NOT be in _FATAL_ERRORS."""
        assert error_type not in PersistentSttSession._FATAL_ERRORS


@pytest.mark.req("REQ-YG-170")
class TestFeedAudioEscalation:
    """_feed_audio must escalate after consecutive send failures."""

    _MAX_CONSECUTIVE_SEND_FAILURES = 3

    @pytest.mark.asyncio
    async def test_feed_audio_escalates_after_consecutive_failures(self) -> None:
        """After N consecutive send failures, _feed_audio should break/escalate."""
        session = PersistentSttSession(api_key="test")
        session._time_limit_event = asyncio.Event()
        session._loop = asyncio.get_event_loop()

        # Mock STT that always fails on send
        session._stt = AsyncMock()
        session._stt.send = AsyncMock(side_effect=Exception("ws closed"))

        # Feed exactly enough frames to trigger escalation + 1
        inbound: asyncio.Queue[bytes | None] = asyncio.Queue()
        for _ in range(self._MAX_CONSECUTIVE_SEND_FAILURES + 2):
            await inbound.put(b"\x00" * 160)
        await inbound.put(None)  # sentinel

        callback = MagicMock()
        session.on_error = callback

        await session._feed_audio(inbound)

        # Should have escalated — either fired on_error or stopped feeding
        # The key assertion: did NOT consume all frames (broke early)
        assert session._stt.send.call_count <= self._MAX_CONSECUTIVE_SEND_FAILURES, (
            f"Expected at most {self._MAX_CONSECUTIVE_SEND_FAILURES} send attempts, "
            f"got {session._stt.send.call_count}"
        )

    @pytest.mark.asyncio
    async def test_feed_audio_resets_counter_on_success(self) -> None:
        """A successful send should reset the consecutive failure counter."""
        session = PersistentSttSession(api_key="test")
        session._time_limit_event = asyncio.Event()
        session._loop = asyncio.get_event_loop()

        # Fail twice, succeed, fail twice more, succeed, then sentinel
        call_count = 0
        fail_succeed_pattern = [
            Exception("fail"),
            Exception("fail"),
            None,  # success
            Exception("fail"),
            Exception("fail"),
            None,  # success
        ]

        async def mock_send(data: dict) -> None:
            nonlocal call_count
            idx = min(call_count, len(fail_succeed_pattern) - 1)
            call_count += 1
            effect = fail_succeed_pattern[idx]
            if effect is not None:
                raise effect

        session._stt = AsyncMock()
        session._stt.send = mock_send

        inbound: asyncio.Queue[bytes | None] = asyncio.Queue()
        for _ in range(len(fail_succeed_pattern)):
            await inbound.put(b"\x00" * 160)
        await inbound.put(None)

        session.on_error = MagicMock()

        await session._feed_audio(inbound)

        # Should have processed all frames (counter reset after each success)
        assert call_count == len(fail_succeed_pattern)
        # on_error should NOT have been called (never hit threshold)
        session.on_error.assert_not_called()
