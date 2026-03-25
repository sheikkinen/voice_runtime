"""RED phase tests for voice_runtime.providers.azure_tts.

Tests AzureTTS provider: speak() pipeline, barge-in interrupt,
disconnected session handling, mark sync. Uses mocked Azure SDK.

NC-161 Phase 3.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch


def _make_session(disconnected: bool = False):
    """Create a mock VoiceSession with working queue methods."""
    session = MagicMock()
    session.is_disconnected = disconnected
    session.put_outbound_sync = MagicMock()
    session.send_mark_and_wait = MagicMock()
    session.tap_agent = MagicMock()
    return session


class TestAzureTTSInit:
    def test_defaults(self):
        from voice_runtime.providers.azure_tts import AzureTTS

        tts = AzureTTS(subscription_key="test-key")
        assert tts._region == "westeurope"
        assert tts._voice_name == "fi-FI-NooraNeural"

    def test_custom_params(self):
        from voice_runtime.providers.azure_tts import AzureTTS

        tts = AzureTTS(
            subscription_key="k",
            region="northeurope",
            voice_name="fi-FI-HarriNeural",
        )
        assert tts._region == "northeurope"
        assert tts._voice_name == "fi-FI-HarriNeural"

    def test_key_from_env(self):
        from voice_runtime.providers.azure_tts import AzureTTS

        with patch.dict("os.environ", {"AZURE_SPEECH_KEY": "env-key"}):
            tts = AzureTTS()
        assert tts._subscription_key == "env-key"


class TestAzureTTSSpeak:
    def test_empty_text_returns_empty(self):
        from voice_runtime.providers.azure_tts import AzureTTS

        tts = AzureTTS(subscription_key="test-key")
        session = _make_session()
        result = tts.speak("", session)
        assert result["last_spoken"] == ""

    def test_disconnected_session_returns_early(self):
        from voice_runtime.providers.azure_tts import AzureTTS

        tts = AzureTTS(subscription_key="test-key")
        session = _make_session(disconnected=True)
        result = tts.speak("hello", session)
        assert result.get("call_disconnected") is True
        assert result["last_spoken"] == ""

    def test_speak_streams_native_mulaw(self):
        """speak() configures Raw8Khz8BitMonoMULaw and streams to session."""
        from voice_runtime.providers.azure_tts import AzureTTS

        tts = AzureTTS(subscription_key="test-key")
        session = _make_session()

        mock_result = MagicMock()
        mock_result.reason = MagicMock()
        mock_result.reason.name = "SynthesizingAudioCompleted"

        # Capture the synthesizing callback and simulate audio events
        synthesizing_callbacks = []

        def capture_connect(callback):
            synthesizing_callbacks.append(callback)

        mock_synthesizer = MagicMock()
        mock_synthesizer.synthesizing.connect = capture_connect
        mock_future = MagicMock()
        mock_future.get.return_value = mock_result
        mock_synthesizer.speak_text_async.return_value = mock_future

        with patch("azure.cognitiveservices.speech.SpeechConfig") as mock_config_cls, \
             patch("azure.cognitiveservices.speech.SpeechSynthesizer", return_value=mock_synthesizer):

            # Before speak_text_async().get() returns, simulate audio chunks
            def trigger_audio(*args, **kwargs):
                for cb in synthesizing_callbacks:
                    evt = MagicMock()
                    evt.result.audio_data = b"\x00" * 320
                    cb(evt)
                    evt2 = MagicMock()
                    evt2.result.audio_data = b"\xff" * 160
                    cb(evt2)
                return mock_future

            mock_synthesizer.speak_text_async = trigger_audio

            result = tts.speak("hello world", session)

        assert result["last_spoken"] == "hello world"
        assert session.put_outbound_sync.call_count == 2
        session.tap_agent.assert_called()
        session.send_mark_and_wait.assert_called_once_with("tts_complete", timeout=30.0)

    def test_barge_in_interrupts_playback(self):
        """stop_event set → return interrupted, call stop_speaking_async."""
        from voice_runtime.providers.azure_tts import AzureTTS

        tts = AzureTTS(subscription_key="test-key")
        session = _make_session()

        stop_event = threading.Event()

        synthesizing_callbacks = []

        def capture_connect(callback):
            synthesizing_callbacks.append(callback)

        mock_synthesizer = MagicMock()
        mock_synthesizer.synthesizing.connect = capture_connect
        mock_result = MagicMock()
        mock_future = MagicMock()
        mock_future.get.return_value = mock_result

        with patch("azure.cognitiveservices.speech.SpeechConfig"), \
             patch("azure.cognitiveservices.speech.SpeechSynthesizer", return_value=mock_synthesizer):

            def trigger_audio(*args, **kwargs):
                for cb in synthesizing_callbacks:
                    # First chunk: OK
                    evt = MagicMock()
                    evt.result.audio_data = b"\x00" * 160
                    cb(evt)
                    # Set stop before second chunk
                    stop_event.set()
                    evt2 = MagicMock()
                    evt2.result.audio_data = b"\xff" * 160
                    cb(evt2)
                return mock_future

            mock_synthesizer.speak_text_async = trigger_audio

            result = tts.speak("hello", session, stop_event=stop_event)

        assert result.get("interrupted") is True
        assert result["last_spoken"] == "hello"

    def test_speak_sets_mulaw_output_format(self):
        """Verify SpeechConfig.set_speech_synthesis_output_format called with mulaw."""
        from voice_runtime.providers.azure_tts import AzureTTS

        tts = AzureTTS(subscription_key="test-key")
        session = _make_session()

        mock_config = MagicMock()
        mock_synthesizer = MagicMock()
        mock_synthesizer.synthesizing.connect = MagicMock()
        mock_result = MagicMock()
        mock_future = MagicMock()
        mock_future.get.return_value = mock_result
        mock_synthesizer.speak_text_async.return_value = mock_future

        with patch("azure.cognitiveservices.speech.SpeechConfig", return_value=mock_config), \
             patch("azure.cognitiveservices.speech.SpeechSynthesizer", return_value=mock_synthesizer), \
             patch("azure.cognitiveservices.speech.SpeechSynthesisOutputFormat") as mock_fmt:

            tts.speak("test", session)

            mock_config.set_speech_synthesis_output_format.assert_called_once_with(
                mock_fmt.Raw8Khz8BitMonoMULaw
            )
