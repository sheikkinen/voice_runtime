"""RED phase tests for voice_runtime.providers.azure_stt.

Tests AzurePersistentStt provider:
- Push stream creation with MULAW 8kHz format
- Continuous recognition lifecycle (start/stop)
- Committed transcript → on_committed callback
- Echo discard window after set_speaking(False)

NC-161 Phase 1 + Phase 2.
NC-166: Simplified — removed barge-in, direct dispatch, per-turn, transcript queue.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# AzurePersistentStt
# ---------------------------------------------------------------------------

class TestAzurePersistentSttInit:
    def test_defaults(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        assert stt._region == "westeurope"
        assert stt._language_code == "fi-FI"
        assert stt._silence_timeout_ms == 1500

    def test_custom_params(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(
            subscription_key="k",
            region="northeurope",
            language_code="en-US",
            silence_timeout_ms=2000,
        )
        assert stt._region == "northeurope"
        assert stt._language_code == "en-US"
        assert stt._silence_timeout_ms == 2000

    def test_key_from_env(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        with patch.dict("os.environ", {"AZURE_SPEECH_KEY": "env-key"}):
            stt = AzurePersistentStt()
        assert stt._subscription_key == "env-key"


class TestAzurePersistentSttStart:
    @pytest.mark.asyncio
    async def test_start_creates_push_stream_and_recognizer(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        inbound = asyncio.Queue()

        with patch("azure.cognitiveservices.speech.SpeechConfig") as mock_config_cls, \
             patch("azure.cognitiveservices.speech.audio.AudioStreamFormat") as mock_fmt_cls, \
             patch("azure.cognitiveservices.speech.audio.PushAudioInputStream") as mock_stream_cls, \
             patch("azure.cognitiveservices.speech.audio.AudioConfig") as mock_audio_cfg_cls, \
             patch("azure.cognitiveservices.speech.SpeechRecognizer") as mock_recognizer_cls:

            mock_recognizer = MagicMock()
            mock_recognizer.start_continuous_recognition_async.return_value = MagicMock()
            mock_recognizer.start_continuous_recognition_async.return_value.get.return_value = None
            mock_recognizer_cls.return_value = mock_recognizer

            mock_push_stream = MagicMock()
            mock_stream_cls.return_value = mock_push_stream

            await stt.start(inbound)

            # Verify MULAW format
            fmt_call = mock_fmt_cls.call_args
            assert fmt_call[1]["samples_per_second"] == 8000
            assert fmt_call[1]["bits_per_sample"] == 8
            assert fmt_call[1]["channels"] == 1

            # Verify recognizer events connected (NC-166: only recognized, no recognizing)
            mock_recognizer.recognized.connect.assert_called_once()

            # Verify continuous recognition started
            mock_recognizer.start_continuous_recognition_async.assert_called_once()

            await stt.stop()


class TestAzurePersistentSttStop:
    @pytest.mark.asyncio
    async def test_stop_cancels_feed_and_stops_recognizer(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        inbound = asyncio.Queue()

        with patch("azure.cognitiveservices.speech.SpeechConfig"), \
             patch("azure.cognitiveservices.speech.audio.AudioStreamFormat"), \
             patch("azure.cognitiveservices.speech.audio.PushAudioInputStream"), \
             patch("azure.cognitiveservices.speech.audio.AudioConfig"), \
             patch("azure.cognitiveservices.speech.SpeechRecognizer") as mock_recognizer_cls:

            mock_recognizer = MagicMock()
            mock_recognizer.start_continuous_recognition_async.return_value = MagicMock()
            mock_recognizer.start_continuous_recognition_async.return_value.get.return_value = None
            mock_recognizer.stop_continuous_recognition_async.return_value = MagicMock()
            mock_recognizer.stop_continuous_recognition_async.return_value.get.return_value = None
            mock_recognizer_cls.return_value = mock_recognizer

            await stt.start(inbound)
            await stt.stop()

            mock_recognizer.stop_continuous_recognition_async.assert_called_once()


class TestAzurePersistentSttTranscripts:
    def test_committed_transcript_fires_on_committed(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        stt._speaking = False
        stt._discard_until = 0.0

        received = []
        stt.on_committed = lambda text: received.append(text)

        mock_result = MagicMock()
        mock_result.text = "Hei maailma"
        mock_evt = MagicMock()
        mock_evt.result = mock_result

        stt._on_committed(mock_evt)
        assert received == ["Hei maailma"]

    def test_no_callback_does_not_raise(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        stt._speaking = False
        stt._discard_until = 0.0

        mock_evt = MagicMock()
        mock_evt.result.text = "hello"
        stt._on_committed(mock_evt)  # must not raise


class TestAzurePersistentSttEchoDiscard:
    @pytest.mark.asyncio
    async def test_set_speaking_false_sets_echo_discard_window(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        stt._speaking = True
        stt.set_speaking(False)

        assert stt._discard_until > time.monotonic()
        assert not stt._speaking

    def test_committed_during_echo_window_discarded(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        stt._speaking = False
        stt._discard_until = time.monotonic() + 10.0  # Far future

        received = []
        stt.on_committed = lambda text: received.append(text)

        mock_evt = MagicMock()
        mock_evt.result.text = "echo text"
        stt._on_committed(mock_evt)

        assert received == []  # Discarded

    def test_committed_during_speaking_discarded(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        stt._speaking = True
        stt._discard_until = 0.0

        received = []
        stt.on_committed = lambda text: received.append(text)

        mock_evt = MagicMock()
        mock_evt.result.text = "should be dropped"
        stt._on_committed(mock_evt)

        assert received == []


class TestAzurePersistentSttFeedAudio:
    @pytest.mark.asyncio
    async def test_feed_writes_to_push_stream(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        mock_push_stream = MagicMock()
        stt._push_stream = mock_push_stream

        inbound = asyncio.Queue()
        inbound.put_nowait(b"\x00" * 160)
        inbound.put_nowait(b"\xff" * 160)
        inbound.put_nowait(None)  # Sentinel

        await stt._feed_audio(inbound)

        assert mock_push_stream.write.call_count == 2
        mock_push_stream.close.assert_called_once()

