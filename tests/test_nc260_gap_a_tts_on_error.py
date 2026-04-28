"""RED tests for NC-260 Gap A: TTS providers must have on_error callback.

When TTS synthesis fails (API error, auth failure, network drop), the
provider must fire on_error so the FSM can react instead of hanging
in a speaking_* state.
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock, patch

import pytest


def _make_session():
    """Create a minimal VoiceSession."""
    from voice_runtime.session import VoiceSession

    s = VoiceSession()
    loop = asyncio.new_event_loop()
    s.set_loop(loop)
    s.signal_ws_connected("test-sid")
    return s, loop


class TestTtsProviderProtocol:
    """TtsProvider protocol must exist and define on_error."""

    def test_tts_provider_protocol_exists(self):
        from voice_runtime.providers import TtsProvider
        assert "on_error" in TtsProvider.__annotations__

    def test_tts_provider_has_speak_method(self):
        from voice_runtime.providers import TtsProvider
        assert "speak" in dir(TtsProvider)


class TestAzureTtsOnError:
    """Azure TTS must fire on_error on synthesis failure."""

    def test_azure_tts_has_on_error_attribute(self):
        from voice_runtime.providers.azure_tts import AzureTTS
        tts = AzureTTS(subscription_key="fake", region="fake")
        assert hasattr(tts, "on_error"), "AzureTTS must have on_error attribute"
        assert tts.on_error is None, "on_error should default to None"

    def test_azure_tts_fires_on_error_on_synthesis_failure(self):
        """When speak_text_async raises, on_error must fire."""
        from voice_runtime.providers.azure_tts import AzureTTS

        tts = AzureTTS(subscription_key="fake", region="fake")
        errors_received = []
        tts.on_error = lambda reason: errors_received.append(reason)

        session, loop = _make_session()

        # Mock SpeechSynthesizer to raise on speak_text_async
        with patch("voice_runtime.providers.azure_tts.speechsdk") as mock_sdk:
            mock_synth = MagicMock()
            mock_synth.speak_text_async.return_value.get.side_effect = Exception(
                "Azure synthesis failed"
            )
            mock_sdk.SpeechSynthesizer.return_value = mock_synth
            mock_sdk.SpeechConfig.return_value = MagicMock()
            mock_sdk.SpeechSynthesisOutputFormat.Raw8Khz8BitMonoMULaw = "raw"

            result = tts.speak("test text", session)

        assert len(errors_received) == 1, (
            f"on_error should fire once, got {len(errors_received)} calls"
        )
        assert "synthesis" in errors_received[0].lower() or "azure" in errors_received[0].lower()
        loop.close()


class TestElevenLabsTtsOnError:
    """ElevenLabs TTS must fire on_error on API failure."""

    def test_elevenlabs_tts_has_on_error_attribute(self):
        from voice_runtime.providers.elevenlabs_tts import ElevenLabsTTS
        tts = ElevenLabsTTS(api_key="fake")
        assert hasattr(tts, "on_error"), "ElevenLabsTTS must have on_error attribute"
        assert tts.on_error is None, "on_error should default to None"

    def test_elevenlabs_tts_fires_on_error_on_api_failure(self):
        """When client.text_to_speech.convert raises, on_error must fire."""
        from voice_runtime.providers.elevenlabs_tts import ElevenLabsTTS

        tts = ElevenLabsTTS(api_key="fake")
        errors_received = []
        tts.on_error = lambda reason: errors_received.append(reason)

        session, loop = _make_session()

        with patch("elevenlabs.ElevenLabs") as mock_cls:
            mock_client = MagicMock()
            mock_client.text_to_speech.convert.side_effect = Exception(
                "ElevenLabs API error"
            )
            mock_cls.return_value = mock_client

            result = tts.speak("test text", session)

        assert len(errors_received) == 1, (
            f"on_error should fire once, got {len(errors_received)} calls"
        )
        loop.close()

    def test_elevenlabs_tts_fires_on_error_on_stream_failure(self):
        """When audio stream iteration raises, on_error must fire."""
        from voice_runtime.providers.elevenlabs_tts import ElevenLabsTTS

        tts = ElevenLabsTTS(api_key="fake")
        errors_received = []
        tts.on_error = lambda reason: errors_received.append(reason)

        session, loop = _make_session()

        def bad_stream():
            yield b"\x00" * 160  # first chunk OK
            raise ConnectionError("stream interrupted")

        with patch("elevenlabs.ElevenLabs") as mock_cls:
            mock_client = MagicMock()
            mock_client.text_to_speech.convert.return_value = bad_stream()
            mock_cls.return_value = mock_client

            result = tts.speak("test text", session)

        assert len(errors_received) == 1, (
            f"on_error should fire on stream failure, got {len(errors_received)}"
        )
        loop.close()
