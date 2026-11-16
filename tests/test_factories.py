"""RED phase tests for voice_runtime factory functions.

Tests create_tts(), create_stt(), create_transport() — the public API.

NC-152 Phase 2.
"""

from __future__ import annotations

import pytest

from tests.conftest import requires_azure


class TestCreateTts:
    def test_default_provider_is_elevenlabs(self):
        from voice_runtime.tts import create_tts

        tts = create_tts()
        assert tts is not None
        assert type(tts).__name__ == "ElevenLabsTTS"

    def test_explicit_elevenlabs(self):
        from voice_runtime.tts import create_tts

        tts = create_tts(provider="elevenlabs")
        assert type(tts).__name__ == "ElevenLabsTTS"

    @requires_azure
    def test_azure_provider(self):
        from voice_runtime.tts import create_tts

        tts = create_tts(provider="azure", subscription_key="test")
        assert type(tts).__name__ == "AzureTTS"

    @pytest.mark.req("VR-013")
    def test_google_provider(self):
        pytest.importorskip(
            "google.cloud.texttospeech", reason="optional google extra not installed"
        )
        from voice_runtime.tts import create_tts

        tts = create_tts(provider="google", project="p")
        assert type(tts).__name__ == "GoogleTTS"

    def test_unknown_provider_raises(self):
        from voice_runtime.tts import create_tts

        with pytest.raises(ValueError, match="Unknown TTS provider"):
            create_tts(provider="nonexistent")


class TestCreateStt:
    def test_default_is_elevenlabs(self):
        from voice_runtime.stt import create_stt

        stt = create_stt()
        assert type(stt).__name__ == "PersistentSttSession"

    def test_explicit_elevenlabs(self):
        from voice_runtime.stt import create_stt

        stt = create_stt(provider="elevenlabs")
        assert type(stt).__name__ == "PersistentSttSession"

    @requires_azure
    def test_azure_provider(self):
        from voice_runtime.stt import create_stt

        stt = create_stt(provider="azure", subscription_key="test")
        assert type(stt).__name__ == "AzurePersistentStt"

    @pytest.mark.req("VR-013")
    def test_google_provider(self):
        pytest.importorskip(
            "google.cloud.speech_v2", reason="optional google extra not installed"
        )
        from voice_runtime.stt import create_stt

        stt = create_stt(provider="google", project="p")
        assert type(stt).__name__ == "GooglePersistentStt"

    @pytest.mark.req("VR-013")
    def test_get_stt_class_google(self):
        pytest.importorskip(
            "google.cloud.speech_v2", reason="optional google extra not installed"
        )
        from voice_runtime.stt import get_stt_class

        assert get_stt_class("google").__name__ == "GooglePersistentStt"

    def test_unknown_provider_raises(self):
        from voice_runtime.stt import create_stt

        with pytest.raises(ValueError, match="Unknown STT provider"):
            create_stt(provider="nonexistent")


class TestGetSmsTransport:
    def test_default_is_twilio(self):
        from voice_runtime.transport import get_sms_transport

        transport = get_sms_transport()
        assert transport is not None

    def test_unknown_transport_raises(self):
        from voice_runtime.transport import get_sms_transport

        with pytest.raises(ValueError, match="Unknown SMS transport"):
            get_sms_transport(provider="nonexistent")
