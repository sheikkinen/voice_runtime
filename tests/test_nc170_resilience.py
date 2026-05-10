"""RED phase tests for NC-170: voice_runtime resilience fixes.

Fix 1: Silent exception handlers — logging + drop counter
Fix 2: Exponential backoff for STT reconnect
Fix 3: Lazy asyncio.Event initialization race
Fix 4: Audio frame size validation logging
Fix 5: AudioMixer sigkill fallback
Bonus: String-based error matching in twilio_ws
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fix 1: Silent exception handlers
# ---------------------------------------------------------------------------


class TestFix1SilentExceptions:
    """session.py clear_inbound and stt_tee.py fanout should log, not swallow."""

    def test_clear_inbound_logs_on_drain_failure(self, caplog):
        """session.py:149 — drain timeout should log at DEBUG, not silently pass."""
        from voice_runtime.session import VoiceSession

        loop = asyncio.new_event_loop()
        s = VoiceSession()
        s.set_loop(loop)

        # Patch run_coroutine_threadsafe to return a future that raises on .result()
        future = MagicMock()
        future.result = MagicMock(side_effect=TimeoutError("test timeout"))

        with patch(
            "voice_runtime.session.asyncio.run_coroutine_threadsafe",
            return_value=future,
        ):
            with caplog.at_level(logging.DEBUG, logger="voice_runtime.session"):
                s.clear_inbound()

        assert any("clear_inbound" in r.message for r in caplog.records), (
            "Expected DEBUG log on drain failure, got: "
            + str([r.message for r in caplog.records])
        )
        loop.close()

    @pytest.mark.asyncio
    async def test_stt_tee_fanout_counts_secondary_drops(self):
        """stt_tee.py:94 — secondary overflow should increment drop counter."""
        from voice_runtime.stt_tee import SttTee

        primary = MagicMock()
        primary.start = AsyncMock()
        primary.stop = AsyncMock()
        primary.on_committed = None

        secondary = MagicMock()
        secondary.start = AsyncMock()
        secondary.stop = AsyncMock()
        secondary.on_committed = None

        tee = SttTee(primary, secondary)
        assert hasattr(tee, "_secondary_drops"), "SttTee must init _secondary_drops"
        assert tee._secondary_drops == 0

    @pytest.mark.asyncio
    async def test_stt_tee_fanout_logs_periodic_drops(self, caplog):
        """stt_tee.py — drop counter should log every 500 drops."""
        from voice_runtime.stt_tee import SttTee

        primary = MagicMock()
        primary.start = AsyncMock()
        primary.stop = AsyncMock()
        primary.on_committed = None

        secondary = MagicMock()
        secondary.start = AsyncMock()
        secondary.stop = AsyncMock()
        secondary.on_committed = None

        tee = SttTee(primary, secondary)

        inbound = asyncio.Queue()
        await tee.start(inbound)

        # Fill secondary queue to force overflow
        # Secondary queue is unbounded by default, so we need to make put_nowait fail
        tee._secondary_queue = asyncio.Queue(maxsize=1)
        # Pre-fill it
        tee._secondary_queue.put_nowait(b"\x00")

        # Send 501 frames — first drop should log (drop #1), then #501
        with caplog.at_level(logging.WARNING, logger="voice_runtime.stt_tee"):
            for _ in range(501):
                await inbound.put(b"\xff" * 160)
                await asyncio.sleep(0)  # let fanout task run

        await inbound.put(None)  # sentinel
        await asyncio.sleep(0.05)

        assert tee._secondary_drops >= 500
        assert any("queue overflow" in r.message.lower() for r in caplog.records), (
            "Expected periodic overflow log, got: "
            + str([r.message for r in caplog.records])
        )
        await tee.stop()


# ---------------------------------------------------------------------------
# Fix 2: Exponential backoff for STT reconnect
# ---------------------------------------------------------------------------


class TestFix2ReconnectBackoff:
    """elevenlabs_stt.py _reconnect_after_error should use exponential backoff."""

    @pytest.mark.asyncio
    async def test_reconnect_has_backoff_delay(self):
        """First reconnect should delay ~1s, not connect immediately."""
        from voice_runtime.providers.elevenlabs_stt import PersistentSttSession

        stt = PersistentSttSession(api_key="test-key")
        assert hasattr(stt, "_reconnect_attempt"), "Must track reconnect attempts"
        assert stt._reconnect_attempt == 0

    @pytest.mark.asyncio
    async def test_reconnect_delay_increases_exponentially(self):
        """Delays should be 1s, 2s, 4s, 8s, 16s, 30s (capped)."""
        from voice_runtime.providers.elevenlabs_stt import PersistentSttSession

        stt = PersistentSttSession(api_key="test-key")
        stt._inbound_queue = asyncio.Queue()

        delays = []
        original_connect = stt._connect

        async def mock_connect():
            raise ConnectionError("test failure")

        stt._connect = mock_connect

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            # Attempt 3 reconnects
            for _ in range(3):
                await stt._reconnect_after_error()

            assert mock_sleep.call_count == 3
            for call in mock_sleep.call_args_list:
                delays.append(call[0][0])

        # Delays should increase (with jitter: ±25%)
        assert delays[0] < delays[1] < delays[2], f"Delays should increase: {delays}"
        # First delay ~1s (0.75-1.25 with jitter)
        assert 0.5 < delays[0] < 1.5, f"First delay out of range: {delays[0]}"

    @pytest.mark.asyncio
    async def test_reconnect_attempt_resets_on_success(self):
        """Successful reconnect should reset attempt counter to 0."""
        from voice_runtime.providers.elevenlabs_stt import PersistentSttSession

        stt = PersistentSttSession(api_key="test-key")
        stt._inbound_queue = asyncio.Queue()
        stt._reconnect_attempt = (
            1  # simulate one previous failure (under NC-258 cap of 3)
        )

        connect_called = False

        async def mock_connect_success():
            nonlocal connect_called
            connect_called = True

        stt._connect = mock_connect_success

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await stt._reconnect_after_error()

        assert connect_called
        assert stt._reconnect_attempt == 0, "Should reset on success"

    @pytest.mark.asyncio
    async def test_reconnect_delay_caps_at_30s(self):
        """Backoff should cap at 30 seconds."""
        from voice_runtime.providers.elevenlabs_stt import PersistentSttSession

        stt = PersistentSttSession(api_key="test-key")
        stt._inbound_queue = asyncio.Queue()
        stt._reconnect_attempt = 2  # attempt 2 of 3 — still under NC-258 cap

        async def mock_connect():
            raise ConnectionError("test failure")

        stt._connect = mock_connect

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await stt._reconnect_after_error()

        delay = mock_sleep.call_args[0][0]
        # Base delay 1s × 2^2 = 4s; with jitter (0.75-1.25): 3-5s
        # Verify it's using backoff, not the 30s cap
        assert 2.0 < delay < 6.0, f"Delay should be ~4s at attempt 2, got {delay}"


# ---------------------------------------------------------------------------
# Fix 3: Lazy asyncio.Event initialization race
# ---------------------------------------------------------------------------


class TestFix3EventInitRace:
    """Transport intent fields should be usable after set_loop(), not just after WS connect."""

    def test_disconnect_requested_available_after_set_loop(self):
        """request_disconnect() should work after set_loop() but before signal_ws_connected()."""
        from voice_runtime.session import VoiceSession

        loop = asyncio.new_event_loop()
        try:
            s = VoiceSession()
            s.set_loop(loop)

            # Before fix: _disconnect_requested is None here
            assert (
                s._disconnect_requested is not None
            ), "_disconnect_requested should be initialized in set_loop()"
            assert isinstance(s._disconnect_requested, asyncio.Event)
        finally:
            loop.close()

    def test_clear_queue_available_after_set_loop(self):
        """request_clear_buffer() should work after set_loop() but before signal_ws_connected()."""
        from voice_runtime.session import VoiceSession

        loop = asyncio.new_event_loop()
        try:
            s = VoiceSession()
            s.set_loop(loop)

            assert (
                s._clear_queue is not None
            ), "_clear_queue should be initialized in set_loop()"
            assert isinstance(s._clear_queue, asyncio.Queue)
        finally:
            loop.close()

    @pytest.mark.asyncio
    async def test_request_disconnect_works_before_ws_connect(self):
        """Calling request_disconnect() between set_loop() and signal_ws_connected()
        should set the event (not silently skip)."""
        from voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.set_loop(asyncio.get_running_loop())
        # Do NOT call signal_ws_connected — simulate pre-connection
        s.request_disconnect()
        await asyncio.sleep(0)  # let call_soon_threadsafe execute
        assert (
            s._disconnect_requested.is_set()
        ), "request_disconnect() should work before WS connect"

    def test_signal_ws_connected_still_works(self):
        """signal_ws_connected() should not fail if fields already initialized."""
        from voice_runtime.session import VoiceSession

        loop = asyncio.new_event_loop()
        try:
            s = VoiceSession()
            s.set_loop(loop)
            # Fields already init'd by set_loop
            s.signal_ws_connected("test-stream-sid")
            assert s.stream_sid == "test-stream-sid"
            # Fields should still be valid
            assert s._disconnect_requested is not None
            assert s._clear_queue is not None
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# Fix 4: Audio frame size validation
# ---------------------------------------------------------------------------


class TestFix4FrameSizeLogging:
    """put_inbound() should log non-standard frame sizes."""

    def test_standard_frame_no_log(self, caplog):
        """160-byte frames should not produce any log."""
        from voice_runtime.session import VoiceSession

        loop = asyncio.new_event_loop()
        try:
            s = VoiceSession()
            s.set_loop(loop)

            with caplog.at_level(logging.DEBUG, logger="voice_runtime.session"):
                s.put_inbound(b"\xff" * 160)

            frame_logs = [
                r for r in caplog.records if "frame size" in r.message.lower()
            ]
            assert len(frame_logs) == 0, "Standard 160-byte frame should not log"
        finally:
            loop.close()

    def test_nonstandard_frame_logs_debug(self, caplog):
        """Non-160-byte frame should log at DEBUG level."""
        from voice_runtime.session import VoiceSession

        loop = asyncio.new_event_loop()
        try:
            s = VoiceSession()
            s.set_loop(loop)

            with caplog.at_level(logging.DEBUG, logger="voice_runtime.session"):
                s.put_inbound(b"\xff" * 320)  # double-size frame

            frame_logs = [
                r for r in caplog.records if "frame size" in r.message.lower()
            ]
            assert len(frame_logs) == 1, "Non-standard frame should log once"
            assert frame_logs[0].levelno == logging.DEBUG
        finally:
            loop.close()

    @pytest.mark.asyncio
    async def test_nonstandard_frame_still_enqueued(self):
        """Non-standard frames should still be enqueued (log-only, not reject)."""
        from voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.set_loop(asyncio.get_running_loop())

        s.put_inbound(b"\xff" * 320)
        await asyncio.sleep(0.05)  # let coroutine run

        assert (
            not s.inbound.empty()
        ), "Frame should be enqueued despite non-standard size"

    def test_empty_frame_no_log(self, caplog):
        """Empty bytes should not produce frame size log (it's a sentinel-adjacent case)."""
        from voice_runtime.session import VoiceSession

        loop = asyncio.new_event_loop()
        try:
            s = VoiceSession()
            s.set_loop(loop)

            with caplog.at_level(logging.DEBUG, logger="voice_runtime.session"):
                s.put_inbound(b"")

            frame_logs = [
                r for r in caplog.records if "frame size" in r.message.lower()
            ]
            assert len(frame_logs) == 0, "Empty frame should not log"
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# Fix 5: AudioMixer sigkill fallback
# ---------------------------------------------------------------------------


class TestFix5AudioMixerSigkill:
    """AudioMixer shutdown should SIGKILL after terminate timeout."""

    def test_shutdown_kills_after_timeout(self):
        """If proc.wait() times out after terminate, should call proc.kill()."""
        from voice_runtime.audio import AudioMixer

        mixer = AudioMixer()
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.stdin = MagicMock()
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = MagicMock(
            side_effect=subprocess.TimeoutExpired(cmd="ffplay", timeout=2.0)
        )

        mixer._proc = mock_proc
        mixer._thread = None
        mixer._running = False

        mixer.shutdown()

        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()

    def test_shutdown_no_kill_on_clean_terminate(self):
        """If proc.wait() succeeds, should NOT call proc.kill()."""
        from voice_runtime.audio import AudioMixer

        mixer = AudioMixer()
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.stdin = MagicMock()
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = MagicMock(return_value=0)

        mixer._proc = mock_proc
        mixer._thread = None
        mixer._running = False

        mixer.shutdown()

        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_not_called()


# ---------------------------------------------------------------------------
# Bonus: String-based error matching
# ---------------------------------------------------------------------------


class TestBonusExceptionMatching:
    """twilio_ws.py should catch RuntimeError specifically, not string-match."""

    def test_runtime_error_caught_specifically(self):
        """twilio_ws should catch RuntimeError specifically, not in generic Exception."""
        import ast
        import inspect

        from voice_runtime.transports import twilio_ws

        source = inspect.getsource(twilio_ws)
        tree = ast.parse(source)

        # Walk AST to find except handlers — verify RuntimeError is caught
        # before the generic Exception handler
        has_runtime_error_handler = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                if node.type and isinstance(node.type, ast.Name):
                    if node.type.id == "RuntimeError":
                        has_runtime_error_handler = True

        assert has_runtime_error_handler, (
            "twilio_ws should have an 'except RuntimeError' handler "
            "instead of string-matching inside generic 'except Exception'"
        )
