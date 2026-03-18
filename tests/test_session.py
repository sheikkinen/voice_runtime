"""RED phase tests for voice_runtime.session — VoiceSession.

Tests the provider-agnostic session coordinator: queue API, mark sync,
lifecycle signals, reset, and optional audio monitoring.

NC-152 Phase 2, Step 1.
"""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Construction and defaults
# ---------------------------------------------------------------------------


class TestVoiceSessionDefaults:
    def test_creates_with_defaults(self):
        from projects.voice_runtime.session import VoiceSession

        s = VoiceSession()
        assert s.call_sid is None
        assert s.stream_sid is None
        assert s.caller_number is None
        assert s.is_disconnected is False

    def test_queues_are_asyncio_queues(self):
        from projects.voice_runtime.session import VoiceSession

        s = VoiceSession()
        assert isinstance(s.inbound, asyncio.Queue)
        assert isinstance(s.outbound, asyncio.Queue)


# ---------------------------------------------------------------------------
# Queue API (thread-safe sync wrappers)
# ---------------------------------------------------------------------------


class TestQueueAPI:
    def test_put_outbound_sync_no_loop_is_noop(self):
        from projects.voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.put_outbound_sync(b"\x01")  # must not raise

    def test_put_inbound_no_loop_is_noop(self):
        from projects.voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.put_inbound(b"\x02")  # must not raise

    def test_put_inbound_none_no_loop_is_noop(self):
        from projects.voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.put_inbound(None)  # must not raise

    def test_put_and_get_outbound_with_loop(self):
        from projects.voice_runtime.session import VoiceSession

        s = VoiceSession()
        loop = asyncio.new_event_loop()
        s.set_loop(loop)
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()
        try:
            s.put_outbound_sync(b"\xab" * 160)
            result = asyncio.run_coroutine_threadsafe(
                s.get_outbound(), loop
            ).result(timeout=2)
            assert result == b"\xab" * 160
        finally:
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=2)

    def test_put_and_get_inbound_with_loop(self):
        from projects.voice_runtime.session import VoiceSession

        s = VoiceSession()
        loop = asyncio.new_event_loop()
        s.set_loop(loop)
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()
        try:
            s.put_inbound(b"\xcd" * 160)
            result = asyncio.run_coroutine_threadsafe(
                s.inbound.get(), loop
            ).result(timeout=2)
            assert result == b"\xcd" * 160
        finally:
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=2)

    def test_clear_inbound_drains_queue(self):
        from projects.voice_runtime.session import VoiceSession

        s = VoiceSession()
        loop = asyncio.new_event_loop()
        s.set_loop(loop)
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()
        try:
            # Put some data
            for _ in range(5):
                s.put_inbound(b"\x00" * 160)
            time.sleep(0.1)  # let enqueues land
            s.clear_inbound()
            # Queue should be empty
            assert s.inbound.empty()
        finally:
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=2)

    def test_clear_inbound_no_loop_is_noop(self):
        from projects.voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.clear_inbound()  # must not raise


# ---------------------------------------------------------------------------
# Mark sync
# ---------------------------------------------------------------------------


class TestMarkSync:
    def test_send_mark_and_wait_no_loop_is_noop(self):
        from projects.voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.send_mark_and_wait("test_mark")  # must not raise (no loop → silent return)

    def test_mark_roundtrip(self):
        """send_mark_and_wait blocks until signal_mark_received is called."""
        from projects.voice_runtime.session import VoiceSession

        s = VoiceSession()
        loop = asyncio.new_event_loop()
        s.set_loop(loop)
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()
        try:
            result = [False]

            def sender():
                s.send_mark_and_wait("tts_complete", timeout=5.0)
                result[0] = True

            send_thread = threading.Thread(target=sender)
            send_thread.start()
            time.sleep(0.1)
            # consume the mark from queue and echo it back
            mark = asyncio.run_coroutine_threadsafe(
                s.get_pending_mark(), loop
            ).result(timeout=2)
            assert mark == "tts_complete"
            s.signal_mark_received("tts_complete")
            send_thread.join(timeout=2)
            assert result[0] is True
        finally:
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=2)

    def test_mark_timeout_raises(self):
        from projects.voice_runtime.session import VoiceSession

        s = VoiceSession()
        loop = asyncio.new_event_loop()
        s.set_loop(loop)
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()
        try:
            with pytest.raises(TimeoutError):
                s.send_mark_and_wait("never_echoed", timeout=0.1)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=2)

    def test_mark_timeout_suppressed_when_disconnected(self):
        from projects.voice_runtime.session import VoiceSession

        s = VoiceSession()
        loop = asyncio.new_event_loop()
        s.set_loop(loop)
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()
        try:
            s.signal_disconnected()
            # Should not raise — disconnected suppresses timeout
            s.send_mark_and_wait("ignored", timeout=0.1)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=2)

    def test_signal_mark_received_unknown_logs_warning(self):
        from projects.voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.signal_mark_received("unknown_mark")  # must not raise


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_signal_disconnected_sets_flag(self):
        from projects.voice_runtime.session import VoiceSession

        s = VoiceSession()
        assert not s.is_disconnected
        s.signal_disconnected()
        assert s.is_disconnected

    def test_signal_disconnected_idempotent(self):
        from projects.voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.signal_disconnected()
        s.signal_disconnected()  # must not raise
        assert s.is_disconnected

    def test_signal_disconnected_unblocks_pending_marks(self):
        from projects.voice_runtime.session import VoiceSession

        s = VoiceSession()
        loop = asyncio.new_event_loop()
        s.set_loop(loop)
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()
        try:
            result = [False]

            def sender():
                s.send_mark_and_wait("blocked_mark", timeout=5.0)
                result[0] = True

            send_thread = threading.Thread(target=sender)
            send_thread.start()
            time.sleep(0.1)
            s.signal_disconnected()  # should unblock
            send_thread.join(timeout=2)
            assert result[0] is True
        finally:
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=2)

    def test_signal_ws_connected(self):
        from projects.voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.signal_ws_connected("stream_123")
        assert s.stream_sid == "stream_123"

    def test_wait_for_ws_connect_success(self):
        from projects.voice_runtime.session import VoiceSession

        s = VoiceSession()

        def connector():
            time.sleep(0.1)
            s.signal_ws_connected("s_456")

        t = threading.Thread(target=connector)
        t.start()
        s.wait_for_ws_connect(timeout=2.0)
        assert s.stream_sid == "s_456"
        t.join()

    def test_wait_for_ws_connect_timeout(self):
        from projects.voice_runtime.session import CallNotAnsweredError, VoiceSession

        s = VoiceSession()
        with pytest.raises(CallNotAnsweredError):
            s.wait_for_ws_connect(timeout=0.1)

    def test_set_loop(self):
        from projects.voice_runtime.session import VoiceSession

        s = VoiceSession()
        loop = asyncio.new_event_loop()
        s.set_loop(loop)
        assert s.loop is loop
        loop.close()


# ---------------------------------------------------------------------------
# Reset (multi-call reuse)
# ---------------------------------------------------------------------------


class TestReset:
    def test_reset_clears_disconnected(self):
        from projects.voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.signal_disconnected()
        assert s.is_disconnected
        s.reset()
        assert not s.is_disconnected

    def test_reset_clears_stream_sid(self):
        from projects.voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.signal_ws_connected("old_stream")
        s.reset()
        assert s.stream_sid is None

    def test_reset_clears_pending_marks(self):
        from projects.voice_runtime.session import VoiceSession

        s = VoiceSession()
        s._pending_marks["stale"] = threading.Event()
        s.reset()
        assert len(s._pending_marks) == 0

    def test_reset_drains_queues(self):
        from projects.voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.inbound.put_nowait(b"\x00")
        s.outbound.put_nowait(b"\x01")
        s.reset()
        assert s.inbound.empty()
        assert s.outbound.empty()

    def test_reset_clears_ws_connected_event(self):
        from projects.voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.signal_ws_connected("old")
        s.reset()
        # Should not be connected after reset
        assert not s._ws_connected.is_set()


# ---------------------------------------------------------------------------
# Audio monitoring (optional mixer)
# ---------------------------------------------------------------------------


class TestMonitoring:
    def test_tap_caller_noop_without_mixer(self):
        from projects.voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.tap_caller(b"\x00" * 160)  # must not raise

    def test_tap_agent_noop_without_mixer(self):
        from projects.voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.tap_agent(b"\x00" * 160)  # must not raise

    def test_set_mixer_enables_taps(self):
        from projects.voice_runtime.session import VoiceSession

        s = VoiceSession()
        mock_mixer = MagicMock()
        s.set_mixer(mock_mixer)
        s.tap_caller(b"\xab" * 160)
        s.tap_agent(b"\xcd" * 160)
        mock_mixer.write_caller.assert_called_once_with(b"\xab" * 160)
        mock_mixer.write_agent.assert_called_once_with(b"\xcd" * 160)

    def test_set_mixer_none_disables_taps(self):
        from projects.voice_runtime.session import VoiceSession

        s = VoiceSession()
        mock_mixer = MagicMock()
        s.set_mixer(mock_mixer)
        s.set_mixer(None)
        s.tap_caller(b"\x00" * 160)
        mock_mixer.write_caller.assert_not_called()


# ---------------------------------------------------------------------------
# Exception classes
# ---------------------------------------------------------------------------


class TestExceptions:
    def test_missing_stream_url_error(self):
        from projects.voice_runtime.session import MissingStreamUrlError

        err = MissingStreamUrlError()
        assert "VOICE_STREAM_URL" in str(err)

    def test_call_not_answered_error(self):
        from projects.voice_runtime.session import CallNotAnsweredError

        err = CallNotAnsweredError(30.0)
        assert "30" in str(err)

    def test_call_hangup_error(self):
        from projects.voice_runtime.session import CallHangupError

        err = CallHangupError()
        assert "hung up" in str(err)
