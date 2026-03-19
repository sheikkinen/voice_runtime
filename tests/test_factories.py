"""RED phase tests for voice_runtime factory functions.

Tests create_tts(), create_stt(), create_transport() — the public API.

NC-152 Phase 2.
"""

from __future__ import annotations

import pytest


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

    def test_unknown_provider_raises(self):
        from voice_runtime.tts import create_tts

        with pytest.raises(ValueError, match="Unknown TTS provider"):
            create_tts(provider="nonexistent")


class TestCreateStt:
    def test_default_is_persistent(self):
        from voice_runtime.stt import create_stt

        stt = create_stt()
        assert type(stt).__name__ == "PersistentSttSession"

    def test_persistent_mode(self):
        from voice_runtime.stt import create_stt

        stt = create_stt(provider="elevenlabs", mode="persistent")
        assert type(stt).__name__ == "PersistentSttSession"

    def test_per_turn_mode(self):
        from voice_runtime.stt import create_stt

        stt = create_stt(provider="elevenlabs", mode="per_turn")
        assert type(stt).__name__ == "PerTurnStt"

    def test_unknown_provider_raises(self):
        from voice_runtime.stt import create_stt

        with pytest.raises(ValueError, match="Unknown STT provider"):
            create_stt(provider="nonexistent")

    def test_unknown_mode_raises(self):
        from voice_runtime.stt import create_stt

        with pytest.raises(ValueError, match="Unknown STT mode"):
            create_stt(provider="elevenlabs", mode="invalid")


class TestCreateTransport:
    def test_default_is_twilio(self):
        from voice_runtime.transport import create_transport

        transport = create_transport()
        assert transport is not None

    def test_unknown_transport_raises(self):
        from voice_runtime.transport import create_transport

        with pytest.raises(ValueError, match="Unknown transport"):
            create_transport(provider="nonexistent")
