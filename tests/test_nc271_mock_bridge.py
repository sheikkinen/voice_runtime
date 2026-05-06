"""NC-271: Mock transport bridge unit tests.

Tests for MockTts on_spoken + send_mark_and_wait, FakeWsBridge,
initiate_mock_call, create_text_relay, /test/inject fail-closed gate.
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock, patch

import pytest


class TestMockTtsOnSpoken:
    """Verify MockTts on_spoken callback and send_mark_and_wait."""

    def _make_session(self):
        """Create a minimal session mock that supports send_mark_and_wait."""
        session = MagicMock()
        session.send_mark_and_wait = MagicMock()
        return session

    def test_on_spoken_default_none(self):
        from voice_runtime.mock.tts import MockTts

        tts = MockTts()
        assert tts.on_spoken is None

    def test_on_spoken_fires_on_speak(self):
        from voice_runtime.mock.tts import MockTts

        relayed: list[str] = []
        tts = MockTts(on_spoken=lambda t: relayed.append(t))
        session = self._make_session()

        tts.speak("hello relay", session)

        assert relayed == ["hello relay"]
        assert tts.spoken == ["hello relay"]

    def test_on_spoken_not_called_when_none(self):
        from voice_runtime.mock.tts import MockTts

        tts = MockTts()
        session = self._make_session()
        # Should not raise
        tts.speak("no relay", session)
        assert tts.spoken == ["no relay"]

    def test_send_mark_and_wait_called(self):
        from voice_runtime.mock.tts import MockTts

        tts = MockTts()
        session = self._make_session()

        tts.speak("mark test", session)

        session.send_mark_and_wait.assert_called_once_with(
            "tts_complete", timeout=10.0
        )

    def test_on_spoken_exception_does_not_break_speak(self):
        from voice_runtime.mock.tts import MockTts

        def bad_callback(text):
            raise RuntimeError("relay failed")

        tts = MockTts(on_spoken=bad_callback)
        session = self._make_session()

        # Should not raise — exception is logged, not propagated
        result = tts.speak("still works", session)
        assert result["last_spoken"] == "still works"
        session.send_mark_and_wait.assert_called_once()

    def test_factory_passes_on_spoken(self):
        from voice_runtime.tts import create_tts

        relayed: list[str] = []
        tts = create_tts(provider="mock", on_spoken=lambda t: relayed.append(t))
        session = self._make_session()

        tts.speak("factory relay", session)
        assert relayed == ["factory relay"]


class TestCreateTextRelay:
    """Verify create_text_relay makes HTTP POST to peer."""

    def test_relay_posts_to_peer(self):
        from voice_runtime.transports.mock_bridge import create_text_relay

        with patch("voice_runtime.transports.mock_bridge.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_resp

            relay = create_text_relay("http://127.0.0.1:8765")
            relay("hello peer")

            mock_client.post.assert_called_once_with(
                "http://127.0.0.1:8765/test/inject",
                json={"text": "hello peer"},
            )


class TestIsMockTransport:
    """Verify is_mock_transport() helper."""

    def test_returns_false_by_default(self):
        from voice_runtime.transports.mock_bridge import is_mock_transport

        with patch.dict("os.environ", {}, clear=True):
            assert is_mock_transport() is False

    def test_returns_true_when_set(self):
        from voice_runtime.transports.mock_bridge import is_mock_transport

        with patch.dict("os.environ", {"TRANSPORT": "mock"}):
            assert is_mock_transport() is True

    def test_case_insensitive(self):
        from voice_runtime.transports.mock_bridge import is_mock_transport

        with patch.dict("os.environ", {"TRANSPORT": "MOCK"}):
            assert is_mock_transport() is True


class TestFakeWsBridge:
    """Verify FakeWsBridge protocol conformance."""

    def test_bridge_creates_valid_ids(self):
        from voice_runtime.transports.mock_bridge import FakeWsBridge

        bridge = FakeWsBridge()
        assert bridge._call_sid.startswith("CAMOCK_")
        assert bridge._stream_sid.startswith("MZ")

    def test_bridge_custom_call_sid(self):
        from voice_runtime.transports.mock_bridge import FakeWsBridge

        bridge = FakeWsBridge(call_sid="CUSTOM123")
        assert bridge._call_sid == "CUSTOM123"


class TestInitiateMockCall:
    """Verify initiate_mock_call sends POST and starts bridge."""

    def test_returns_call_sid_and_posts_incoming(self):
        from voice_runtime.transports.mock_bridge import (
            _active_bridges,
            initiate_mock_call,
        )

        with (
            patch("voice_runtime.transports.mock_bridge.httpx.post") as mock_post,
            patch(
                "voice_runtime.transports.mock_bridge.FakeWsBridge"
            ) as mock_bridge_cls,
        ):
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_post.return_value = mock_resp
            mock_bridge = MagicMock()
            mock_bridge_cls.return_value = mock_bridge

            call_sid = initiate_mock_call("http://127.0.0.1:8765")

            assert call_sid.startswith("CAMOCK_")
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert "/incoming" in call_args[0][0]
            mock_bridge.start.assert_called_once()

            # Cleanup
            _active_bridges.clear()
