"""NC-267: Mock provider protocol conformance and functional tests.

RED phase: These tests define the contract before verifying behavior.
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock

import pytest

from voice_runtime.providers import SttProvider, TtsProvider

# --- Protocol catalogs ---

STT_REQUIRED_METHODS = ["set_speaking", "start", "stop"]
STT_REQUIRED_ATTRIBUTES = ["on_committed", "on_recognizing", "on_error"]

TTS_REQUIRED_METHODS = ["speak"]
TTS_REQUIRED_ATTRIBUTES = ["on_error"]


# ---- MockTts Protocol Conformance ----


class TestMockTtsProtocol:
    """Verify MockTts satisfies TtsProvider Protocol."""

    def test_has_all_required_methods(self):
        from voice_runtime.mock.tts import MockTts

        tts = MockTts()
        for method in TTS_REQUIRED_METHODS:
            assert callable(getattr(tts, method, None)), (
                f"MockTts missing method: {method}"
            )

    def test_has_all_required_attributes(self):
        from voice_runtime.mock.tts import MockTts

        tts = MockTts()
        for attr in TTS_REQUIRED_ATTRIBUTES:
            assert hasattr(tts, attr), f"MockTts missing attribute: {attr}"

    def test_on_error_default_is_none(self):
        from voice_runtime.mock.tts import MockTts

        tts = MockTts()
        assert tts.on_error is None


# ---- MockTts Functional Tests ----


class TestMockTtsFunctional:
    """Verify MockTts records speech and returns expected results."""

    def _make_session(self):
        session = MagicMock()
        session.send_mark_and_wait = MagicMock()
        return session

    def test_speak_records_text(self):
        from voice_runtime.mock.tts import MockTts

        tts = MockTts()
        session = self._make_session()
        result = tts.speak("hello", session)

        assert tts.spoken == ["hello"]
        assert result["last_spoken"] == "hello"
        assert result["interrupted"] is False

    def test_speak_multiple(self):
        from voice_runtime.mock.tts import MockTts

        tts = MockTts()
        session = self._make_session()
        tts.speak("first", session)
        tts.speak("second", session)

        assert tts.spoken == ["first", "second"]

    def test_speak_with_stop_event_set(self):
        from voice_runtime.mock.tts import MockTts

        tts = MockTts()
        session = self._make_session()
        stop = threading.Event()
        stop.set()
        result = tts.speak("interrupted", session, stop_event=stop)

        assert result["interrupted"] is True
        assert tts.spoken == ["interrupted"]

    def test_factory_creates_mock_tts(self):
        from voice_runtime.tts import create_tts

        tts = create_tts(provider="mock")
        assert type(tts).__name__ == "MockTts"

    def test_factory_passes_kwargs(self):
        from voice_runtime.tts import create_tts

        tts = create_tts(provider="mock", voice_id="test-voice")
        assert tts._kwargs["voice_id"] == "test-voice"


# ---- MockStt Protocol Conformance ----


class TestMockSttProtocol:
    """Verify MockStt satisfies SttProvider Protocol."""

    def test_has_all_required_methods(self):
        from voice_runtime.mock.stt import MockStt

        stt = MockStt()
        for method in STT_REQUIRED_METHODS:
            assert callable(getattr(stt, method, None)), (
                f"MockStt missing method: {method}"
            )

    def test_has_all_required_attributes(self):
        from voice_runtime.mock.stt import MockStt

        stt = MockStt()
        for attr in STT_REQUIRED_ATTRIBUTES:
            assert hasattr(stt, attr), f"MockStt missing attribute: {attr}"

    def test_on_committed_default_is_none(self):
        from voice_runtime.mock.stt import MockStt

        stt = MockStt()
        assert stt.on_committed is None

    def test_on_recognizing_default_is_none(self):
        from voice_runtime.mock.stt import MockStt

        stt = MockStt()
        assert stt.on_recognizing is None

    def test_on_error_default_is_none(self):
        from voice_runtime.mock.stt import MockStt

        stt = MockStt()
        assert stt.on_error is None


# ---- MockStt Functional Tests ----


class TestMockSttFunctional:
    """Verify MockStt inject/commit cycle works."""

    @pytest.fixture()
    def stt(self):
        from voice_runtime.mock.stt import MockStt

        return MockStt()

    def test_inject_fires_on_committed(self, stt):
        """Injected text triggers on_committed callback."""
        committed: list[str] = []
        stt.on_committed = lambda text: committed.append(text)
        inbound: asyncio.Queue[bytes | None] = asyncio.Queue()

        async def run():
            task = asyncio.create_task(stt.start(inbound))
            # Give the loop a cycle to enter start()
            await asyncio.sleep(0.05)
            stt.inject("hello from script")
            # Wait for the callback to fire
            await asyncio.sleep(0.2)
            await stt.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        assert committed == ["hello from script"]

    def test_inject_multiple(self, stt):
        """Multiple injects fire on_committed in order."""
        committed: list[str] = []
        stt.on_committed = lambda text: committed.append(text)
        inbound: asyncio.Queue[bytes | None] = asyncio.Queue()

        async def run():
            task = asyncio.create_task(stt.start(inbound))
            await asyncio.sleep(0.05)
            stt.inject("one")
            stt.inject("two")
            await asyncio.sleep(0.3)
            await stt.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        assert committed == ["one", "two"]

    def test_set_speaking_is_noop(self, stt):
        """set_speaking should not raise."""
        stt.set_speaking(True)
        stt.set_speaking(False)

    def test_factory_creates_mock_stt(self):
        from voice_runtime.stt import create_stt

        stt = create_stt(provider="mock")
        assert type(stt).__name__ == "MockStt"

    def test_get_stt_class_returns_mock(self):
        from voice_runtime.stt import get_stt_class

        cls = get_stt_class(provider="mock")
        assert cls.__name__ == "MockStt"

    def test_inject_cross_thread(self, stt):
        """inject() from another thread fires on_committed."""
        committed: list[str] = []
        stt.on_committed = lambda text: committed.append(text)
        inbound: asyncio.Queue[bytes | None] = asyncio.Queue()

        async def run():
            task = asyncio.create_task(stt.start(inbound))
            await asyncio.sleep(0.05)

            # Inject from a separate thread
            def injector():
                stt.inject("from-thread")

            t = threading.Thread(target=injector)
            t.start()
            t.join(timeout=1.0)

            await asyncio.sleep(0.2)
            await stt.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        assert committed == ["from-thread"]
