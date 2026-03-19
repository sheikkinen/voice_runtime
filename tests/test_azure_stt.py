"""RED phase tests for voice_runtime.providers.azure_stt.

Tests AzurePersistentStt and AzurePerTurnStt providers:
- Push stream creation with MULAW 8kHz format
- Continuous recognition lifecycle (start/stop)
- Partial transcript → barge-in event
- Committed transcript → next_transcript()
- Echo discard window after set_speaking(False)
- Per-turn recognize_once_async

NC-161 Phase 1 + Phase 2.
"""

from __future__ import annotations

import asyncio
import threading
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

            # Verify recognizer events connected
            mock_recognizer.recognizing.connect.assert_called_once()
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
    @pytest.mark.asyncio
    async def test_committed_transcript_available_via_next_transcript(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        stt._loop = asyncio.get_running_loop()
        stt._speaking = False
        stt._discard_until = 0.0

        # Simulate committed transcript callback
        mock_result = MagicMock()
        mock_result.text = "Hei maailma"
        mock_result.reason = MagicMock()  # ResultReason.RecognizedSpeech
        mock_evt = MagicMock()
        mock_evt.result = mock_result

        # Put transcript then fetch
        stt._on_committed(mock_evt)
        result = await stt.next_transcript(timeout=1.0)
        assert result == "Hei maailma"

    @pytest.mark.asyncio
    async def test_next_transcript_timeout_returns_none(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        result = await stt.next_transcript(timeout=0.1)
        assert result is None


class TestAzurePersistentSttBargeIn:
    @pytest.mark.asyncio
    async def test_partial_during_speaking_fires_barge_in(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        stt._loop = asyncio.get_running_loop()
        stt._speaking = True

        event = stt.arm_barge_in()
        assert not event.is_set()

        # Simulate partial transcript callback with meaningful text
        mock_evt = MagicMock()
        mock_evt.result.text = "Hei kuule"
        stt._on_partial(mock_evt)

        # Event should fire (via call_soon_threadsafe)
        await asyncio.sleep(0.05)
        assert event.is_set()

    @pytest.mark.asyncio
    async def test_short_partial_does_not_fire_barge_in(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        stt._loop = asyncio.get_running_loop()
        stt._speaking = True

        event = stt.arm_barge_in()

        mock_evt = MagicMock()
        mock_evt.result.text = "H"  # Too short
        stt._on_partial(mock_evt)

        await asyncio.sleep(0.05)
        assert not event.is_set()

    @pytest.mark.asyncio
    async def test_partial_while_not_speaking_ignored(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        stt._loop = asyncio.get_running_loop()
        stt._speaking = False

        event = stt.arm_barge_in()

        mock_evt = MagicMock()
        mock_evt.result.text = "Hei maailma"
        stt._on_partial(mock_evt)

        await asyncio.sleep(0.05)
        assert not event.is_set()


class TestAzurePersistentSttEchoDiscard:
    @pytest.mark.asyncio
    async def test_set_speaking_false_sets_echo_discard_window(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        stt._speaking = True
        stt.set_speaking(False)

        assert stt._discard_until > time.monotonic()
        assert not stt._speaking

    @pytest.mark.asyncio
    async def test_committed_during_echo_window_discarded(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        stt._loop = asyncio.get_running_loop()
        stt._speaking = False
        stt._discard_until = time.monotonic() + 10.0  # Far future

        mock_evt = MagicMock()
        mock_evt.result.text = "echo text"
        mock_evt.result.reason = MagicMock()
        stt._on_committed(mock_evt)

        result = await stt.next_transcript(timeout=0.1)
        assert result is None  # Discarded

    @pytest.mark.asyncio
    async def test_committed_during_speaking_discarded(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        stt._loop = asyncio.get_running_loop()
        stt._speaking = True
        stt._discard_until = 0.0

        mock_evt = MagicMock()
        mock_evt.result.text = "should be dropped"
        mock_evt.result.reason = MagicMock()
        stt._on_committed(mock_evt)

        result = await stt.next_transcript(timeout=0.1)
        assert result is None


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


# ---------------------------------------------------------------------------
# AzurePerTurnStt
# ---------------------------------------------------------------------------

class TestAzurePerTurnSttInit:
    def test_defaults(self):
        from voice_runtime.providers.azure_stt import AzurePerTurnStt

        stt = AzurePerTurnStt(subscription_key="test-key")
        assert stt._region == "westeurope"
        assert stt._language_code == "fi-FI"


class TestAzurePerTurnSttListen:
    def test_listen_returns_transcript(self):
        from voice_runtime.providers.azure_stt import AzurePerTurnStt

        import azure.cognitiveservices.speech as speechsdk

        stt = AzurePerTurnStt(subscription_key="test-key")

        mock_session = MagicMock()
        mock_session.is_disconnected = False
        loop = asyncio.new_event_loop()
        mock_session.loop = loop

        mock_result = MagicMock()
        mock_result.text = "Hello world"
        mock_result.reason = speechsdk.ResultReason.RecognizedSpeech

        with patch("azure.cognitiveservices.speech.SpeechConfig"), \
             patch("azure.cognitiveservices.speech.audio.AudioStreamFormat"), \
             patch("azure.cognitiveservices.speech.audio.PushAudioInputStream") as mock_stream_cls, \
             patch("azure.cognitiveservices.speech.audio.AudioConfig"), \
             patch("azure.cognitiveservices.speech.SpeechRecognizer") as mock_recognizer_cls:

            mock_recognizer = MagicMock()
            mock_future = MagicMock()
            mock_future.get.return_value = mock_result
            mock_recognizer.recognize_once_async.return_value = mock_future
            mock_recognizer_cls.return_value = mock_recognizer

            mock_push_stream = MagicMock()
            mock_stream_cls.return_value = mock_push_stream

            # Feed a chunk then sentinel so feed thread exits
            mock_session.inbound = asyncio.Queue()
            mock_session.inbound.put_nowait(b"\x00" * 160)
            mock_session.inbound.put_nowait(None)

            result = stt.listen(mock_session, timeout=2.0)

        loop.close()
        assert result == "Hello world"

    def test_listen_no_loop_returns_empty(self):
        from voice_runtime.providers.azure_stt import AzurePerTurnStt

        stt = AzurePerTurnStt(subscription_key="test-key")
        mock_session = MagicMock()
        mock_session.loop = None

        result = stt.listen(mock_session)
        assert result == ""

    def test_listen_no_match_returns_empty(self):
        from voice_runtime.providers.azure_stt import AzurePerTurnStt

        stt = AzurePerTurnStt(subscription_key="test-key")

        mock_session = MagicMock()
        mock_session.is_disconnected = False
        loop = asyncio.new_event_loop()
        mock_session.loop = loop

        import azure.cognitiveservices.speech as speechsdk

        mock_result = MagicMock()
        mock_result.text = ""
        mock_result.reason = speechsdk.ResultReason.NoMatch

        with patch("azure.cognitiveservices.speech.SpeechConfig"), \
             patch("azure.cognitiveservices.speech.audio.AudioStreamFormat"), \
             patch("azure.cognitiveservices.speech.audio.PushAudioInputStream"), \
             patch("azure.cognitiveservices.speech.audio.AudioConfig"), \
             patch("azure.cognitiveservices.speech.SpeechRecognizer") as mock_recognizer_cls:

            mock_recognizer = MagicMock()
            mock_future = MagicMock()
            mock_future.get.return_value = mock_result
            mock_recognizer.recognize_once_async.return_value = mock_future
            mock_recognizer_cls.return_value = mock_recognizer

            mock_session.inbound = asyncio.Queue()
            mock_session.inbound.put_nowait(None)

            result = stt.listen(mock_session, timeout=2.0)

        loop.close()
        assert result == ""
