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
        from voice_runtime.session import VoiceSession

        s = VoiceSession()
        assert s.call_sid is None
        assert s.stream_sid is None
        assert s.caller_number is None
        assert s.is_disconnected is False

    def test_queues_are_asyncio_queues(self):
        from voice_runtime.session import VoiceSession

        s = VoiceSession()
        assert isinstance(s.inbound, asyncio.Queue)
        assert isinstance(s.outbound, asyncio.Queue)


# ---------------------------------------------------------------------------
# Queue API (thread-safe sync wrappers)
# ---------------------------------------------------------------------------


class TestQueueAPI:
    def test_put_outbound_sync_no_loop_is_noop(self):
        from voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.put_outbound_sync(b"\x01")  # must not raise

    def test_put_inbound_no_loop_is_noop(self):
        from voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.put_inbound(b"\x02")  # must not raise

    def test_put_inbound_none_no_loop_is_noop(self):
        from voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.put_inbound(None)  # must not raise

    def test_put_and_get_outbound_with_loop(self):
        from voice_runtime.session import VoiceSession

        s = VoiceSession()
        loop = asyncio.new_event_loop()
        s.set_loop(loop)
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()
        try:
            s.put_outbound_sync(b"\xab" * 160)
            result = asyncio.run_coroutine_threadsafe(s.get_outbound(), loop).result(
                timeout=2
            )
            assert result == b"\xab" * 160
        finally:
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=2)

    def test_put_and_get_inbound_with_loop(self):
        from voice_runtime.session import VoiceSession

        s = VoiceSession()
        loop = asyncio.new_event_loop()
        s.set_loop(loop)
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()
        try:
            s.put_inbound(b"\xcd" * 160)
            result = asyncio.run_coroutine_threadsafe(s.inbound.get(), loop).result(
                timeout=2
            )
            assert result == b"\xcd" * 160
        finally:
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=2)

    def test_clear_inbound_drains_queue(self):
        from voice_runtime.session import VoiceSession

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
        from voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.clear_inbound()  # must not raise


# ---------------------------------------------------------------------------
# Mark sync
# ---------------------------------------------------------------------------


class TestMarkSync:
    def test_send_mark_and_wait_no_loop_is_noop(self):
        from voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.send_mark_and_wait("test_mark")  # must not raise (no loop → silent return)

    def test_mark_roundtrip(self):
        """send_mark_and_wait blocks until signal_mark_received is called.

        NC-236: the wire-level mark is a unique suffixed string
        (``tts_complete__<8hex>``), not the logical ``tts_complete`` label.
        The transport must echo back the exact string it received.
        """
        from voice_runtime.session import VoiceSession

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
            # consume the mark from queue and echo it back verbatim
            mark = asyncio.run_coroutine_threadsafe(s.get_pending_mark(), loop).result(
                timeout=2
            )
            assert mark.startswith(
                "tts_complete__"
            ), f"expected unique-suffixed mark, got {mark!r}"
            suffix = mark.removeprefix("tts_complete__")
            assert len(suffix) == 8 and all(
                c in "0123456789abcdef" for c in suffix
            ), f"expected 8 hex chars after '__', got {suffix!r}"
            s.signal_mark_received(mark)
            send_thread.join(timeout=2)
            assert result[0] is True
        finally:
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=2)

    def test_mark_timeout_raises(self):
        from voice_runtime.session import VoiceSession

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
        from voice_runtime.session import VoiceSession

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
        from voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.signal_mark_received("unknown_mark")  # must not raise


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_signal_disconnected_sets_flag(self):
        from voice_runtime.session import VoiceSession

        s = VoiceSession()
        assert not s.is_disconnected
        s.signal_disconnected()
        assert s.is_disconnected

    def test_signal_disconnected_idempotent(self):
        from voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.signal_disconnected()
        s.signal_disconnected()  # must not raise
        assert s.is_disconnected

    def test_signal_disconnected_unblocks_pending_marks(self):
        from voice_runtime.session import VoiceSession

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
        from voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.signal_ws_connected("stream_123")
        assert s.stream_sid == "stream_123"

    def test_wait_for_ws_connect_success(self):
        from voice_runtime.session import VoiceSession

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
        from voice_runtime.session import CallNotAnsweredError, VoiceSession

        s = VoiceSession()
        with pytest.raises(CallNotAnsweredError):
            s.wait_for_ws_connect(timeout=0.1)

    def test_set_loop(self):
        from voice_runtime.session import VoiceSession

        s = VoiceSession()
        loop = asyncio.new_event_loop()
        s.set_loop(loop)
        assert s.loop is loop
        loop.close()


# ---------------------------------------------------------------------------
# Reset (multi-call reuse)
# ---------------------------------------------------------------------------


class TestResetMarkSafety:
    """NC-167: reset() must not crash or hang threads waiting on marks.

    Three bugs observed in production logs (2026-03-20):

    Bug 1 (KeyError): reset() clears _pending_marks while send_mark_and_wait
    is blocked on event.wait(). On timeout, `del _pending_marks[name]` raises
    KeyError because reset() already cleared the dict.

    Bug 2 (hang): reset() clears _pending_marks without setting events first.
    Waiting threads block for up to 30s until timeout expires.

    Bug 3 (stale command): reset() clears _disconnected before unblocking
    waiters. A stale speak command from the previous call sees
    is_disconnected=False and proceeds to TTS on the new call's session.
    """

    def test_reset_during_mark_wait_no_keyerror(self):
        """Bug 1: reset() during send_mark_and_wait must not raise KeyError.

        Observed: Bridge handler 'speak' failed: 'tts_complete'
        (KeyError str repr matches the log exactly)
        """
        from voice_runtime.session import VoiceSession

        s = VoiceSession()
        loop = asyncio.new_event_loop()
        s.set_loop(loop)
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        errors: list[Exception] = []

        def waiter():
            try:
                s.send_mark_and_wait("tts_complete", timeout=1.0)
            except (KeyError, TimeoutError):
                # KeyError = the bug; TimeoutError acceptable if disconnected
                errors.append(KeyError("_pending_marks cleared during wait"))
            except Exception as e:
                errors.append(e)

        wait_thread = threading.Thread(target=waiter, daemon=True)
        wait_thread.start()
        time.sleep(0.1)  # let waiter register and block

        # Simulate new call arriving — reset clears pending marks
        s.reset()
        wait_thread.join(timeout=3.0)

        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)

        assert not wait_thread.is_alive(), "waiter hung — reset didn't unblock"
        assert not errors, f"waiter crashed: {errors}"

    def test_reset_unblocks_pending_mark_waiters(self):
        """Bug 2: reset() must set all pending events before clearing.

        Without this, threads block for up to 30s until timeout expires.
        The waiter must return within 1s of reset().
        """
        from voice_runtime.session import VoiceSession

        s = VoiceSession()
        loop = asyncio.new_event_loop()
        s.set_loop(loop)
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        returned = threading.Event()

        def waiter():
            try:
                s.send_mark_and_wait("tts_complete", timeout=30.0)
            except Exception:
                pass
            returned.set()

        wait_thread = threading.Thread(target=waiter, daemon=True)
        wait_thread.start()
        time.sleep(0.1)

        t0 = time.monotonic()
        s.reset()
        returned.wait(timeout=2.0)
        elapsed = time.monotonic() - t0

        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)

        assert returned.is_set(), "waiter not unblocked — hung for 30s"
        assert elapsed < 1.0, (
            f"reset() took {elapsed:.2f}s to unblock waiter — "
            "must set events before clearing"
        )

    def test_reset_keeps_disconnected_until_marks_unblocked(self):
        """Bug 3: _disconnected must remain True until pending marks unblock.

        If reset() clears _disconnected before setting pending events,
        a stale command sees is_disconnected=False and proceeds with
        TTS on the new call's session.
        """
        from voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.signal_disconnected()
        assert s.is_disconnected

        # Simulate a pending mark from a stale speak command
        event = threading.Event()
        s._pending_marks["tts_complete"] = event

        disconnected_during_unblock: list[bool] = []

        original_set = event.set

        def spy_set():
            # When the event is set, record whether disconnected is still True
            disconnected_during_unblock.append(s.is_disconnected)
            original_set()

        event.set = spy_set

        s.reset()

        assert (
            disconnected_during_unblock
        ), "reset() did not set pending events — Bug 2 not fixed"
        assert disconnected_during_unblock[0] is True, (
            "reset() cleared _disconnected before setting pending events — "
            "stale commands will see is_disconnected=False and proceed with TTS"
        )


class TestReset:
    def test_reset_clears_disconnected(self):
        from voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.signal_disconnected()
        assert s.is_disconnected
        s.reset()
        assert not s.is_disconnected

    def test_reset_clears_stream_sid(self):
        from voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.signal_ws_connected("old_stream")
        s.reset()
        assert s.stream_sid is None

    def test_reset_clears_pending_marks(self):
        from voice_runtime.session import VoiceSession

        s = VoiceSession()
        s._pending_marks["stale"] = threading.Event()
        s.reset()
        assert len(s._pending_marks) == 0

    def test_reset_drains_queues(self):
        from voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.inbound.put_nowait(b"\x00")
        s.outbound.put_nowait(b"\x01")
        s.reset()
        assert s.inbound.empty()
        assert s.outbound.empty()

    def test_reset_clears_ws_connected_event(self):
        from voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.signal_ws_connected("old")
        s.reset()
        # Should not be connected after reset
        assert not s._ws_connected.is_set()

    def test_reset_stops_stt_before_clearing(self):
        """BUG: reset() sets stt=None without stopping STT first.

        This orphans the PersistentSttSession — its feed_task and Scribe
        WebSocket remain alive, consuming from the inbound queue and
        potentially interfering with the next call's STT session.

        When no event loop is available, the feed_task should be cancelled.
        When an event loop is running, stop() is scheduled via
        run_coroutine_threadsafe (tested implicitly via production path).
        """
        from unittest.mock import MagicMock

        from voice_runtime.session import VoiceSession

        # Without event loop — feed_task should be cancelled directly
        s = VoiceSession()
        mock_stt = MagicMock()
        mock_stt._feed_task = MagicMock()
        s.stt = mock_stt
        s.reset()
        mock_stt._feed_task.cancel.assert_called_once()
        assert s.stt is None

    def test_reset_with_loop_schedules_async_stop(self):
        """When event loop is available, reset() schedules stt.stop() async."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from voice_runtime.session import VoiceSession

        s = VoiceSession()
        loop = MagicMock()
        loop.is_running.return_value = True
        s._loop = loop
        mock_stt = MagicMock()
        mock_stt.stop = AsyncMock()
        s.stt = mock_stt

        with patch(
            "voice_runtime.session.asyncio.run_coroutine_threadsafe"
        ) as mock_rcts:
            s.reset()
            mock_rcts.assert_called_once()
            # Verify the loop argument is correct
            _, kwargs = mock_rcts.call_args
            args = mock_rcts.call_args[0]
            assert args[1] is loop

        assert s.stt is None


# ---------------------------------------------------------------------------
# Audio monitoring (optional mixer)
# ---------------------------------------------------------------------------


class TestMonitoring:
    def test_tap_caller_noop_without_mixer(self):
        from voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.tap_caller(b"\x00" * 160)  # must not raise

    def test_tap_agent_noop_without_mixer(self):
        from voice_runtime.session import VoiceSession

        s = VoiceSession()
        s.tap_agent(b"\x00" * 160)  # must not raise

    def test_set_mixer_enables_taps(self):
        from voice_runtime.session import VoiceSession

        s = VoiceSession()
        mock_mixer = MagicMock()
        s.set_mixer(mock_mixer)
        s.tap_caller(b"\xab" * 160)
        s.tap_agent(b"\xcd" * 160)
        mock_mixer.write_caller.assert_called_once_with(b"\xab" * 160)
        mock_mixer.write_agent.assert_called_once_with(b"\xcd" * 160)

    def test_set_mixer_none_disables_taps(self):
        from voice_runtime.session import VoiceSession

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
        from voice_runtime.session import MissingStreamUrlError

        err = MissingStreamUrlError()
        assert "VOICE_STREAM_URL" in str(err)

    def test_call_not_answered_error(self):
        from voice_runtime.session import CallNotAnsweredError

        err = CallNotAnsweredError(30.0)
        assert "30" in str(err)

    def test_call_hangup_error(self):
        from voice_runtime.session import CallHangupError

        err = CallHangupError()
        assert "hung up" in str(err)
