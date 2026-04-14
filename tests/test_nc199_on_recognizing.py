"""NC-199: on_recognizing callback — RED phase tests.

Provider fires on_recognizing for every interim/partial transcript.
SttTee proxies on_recognizing to primary only.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# SttProvider Protocol: on_recognizing attribute
# ---------------------------------------------------------------------------


@pytest.mark.req("NC-199")
class TestSttProviderOnRecognizing:
    """SttProvider should have on_recognizing after NC-199."""

    def test_protocol_has_on_recognizing(self):
        from voice_runtime.providers import SttProvider

        assert "on_recognizing" in SttProvider.__annotations__, (
            "SttProvider missing on_recognizing attribute"
        )


# ---------------------------------------------------------------------------
# AzurePersistentStt: on_recognizing callback
# ---------------------------------------------------------------------------


@pytest.mark.req("NC-199")
class TestAzureOnRecognizing:
    """Azure fires on_recognizing for recognizing events."""

    def test_has_on_recognizing_attribute(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        assert hasattr(stt, "on_recognizing")
        assert stt.on_recognizing is None

    def test_fires_on_recognizing_callback(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        received = []
        stt.on_recognizing = lambda text: received.append(text)

        evt = MagicMock()
        evt.result.text = "partial text"
        stt._on_recognizing(evt)

        assert received == ["partial text"]

    def test_skips_during_speaking(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        received = []
        stt.on_recognizing = lambda text: received.append(text)
        stt._speaking = True

        evt = MagicMock()
        evt.result.text = "should drop"
        stt._on_recognizing(evt)

        assert received == []

    def test_skips_empty_text(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        received = []
        stt.on_recognizing = lambda text: received.append(text)

        evt = MagicMock()
        evt.result.text = "  "
        stt._on_recognizing(evt)

        assert received == []


# ---------------------------------------------------------------------------
# ElevenLabs: on_recognizing callback
# ---------------------------------------------------------------------------


@pytest.mark.req("NC-199")
class TestElevenLabsOnRecognizing:
    """ElevenLabs fires on_recognizing for partial_transcript events."""

    def test_has_on_recognizing_attribute(self):
        """Mock elevenlabs before importing."""
        import sys
        from enum import Enum

        originals = {}
        mods = ["elevenlabs", "elevenlabs.realtime", "elevenlabs.realtime.scribe"]
        for m in mods:
            originals[m] = sys.modules.get(m)
            sys.modules[m] = MagicMock()

        class MockAudioFormat(Enum):
            ULAW_8000 = "ulaw_8000"

        class MockCommitStrategy(Enum):
            VAD = "vad"

        sys.modules["elevenlabs.realtime.scribe"].AudioFormat = MockAudioFormat
        sys.modules["elevenlabs.realtime.scribe"].CommitStrategy = MockCommitStrategy

        # Clear cached imports
        to_clear = [k for k in sys.modules if k.startswith("voice_runtime.providers.elevenlabs")]
        for k in to_clear:
            del sys.modules[k]

        try:
            from voice_runtime.providers.elevenlabs_stt import PersistentSttSession

            stt = PersistentSttSession(api_key="test-key")
            assert hasattr(stt, "on_recognizing")
            assert stt.on_recognizing is None
        finally:
            for m, orig in originals.items():
                if orig is None:
                    sys.modules.pop(m, None)
                else:
                    sys.modules[m] = orig
            to_clear = [k for k in sys.modules if k.startswith("voice_runtime.providers.elevenlabs")]
            for k in to_clear:
                del sys.modules[k]


# ---------------------------------------------------------------------------
# SttTee: on_recognizing proxy
# ---------------------------------------------------------------------------


@pytest.mark.req("NC-199")
class TestSttTeeOnRecognizing:
    """SttTee proxies on_recognizing to primary only."""

    def test_proxy_get(self):
        from voice_runtime.stt_tee import SttTee

        primary = MagicMock()
        secondary = MagicMock()
        primary.on_recognizing = "primary_cb"

        tee = SttTee(primary, secondary)
        assert tee.on_recognizing == "primary_cb"

    def test_proxy_set(self):
        from voice_runtime.stt_tee import SttTee

        primary = MagicMock()
        secondary = MagicMock()

        tee = SttTee(primary, secondary)
        cb = lambda text: None  # noqa: E731
        tee.on_recognizing = cb
        assert primary.on_recognizing is cb
