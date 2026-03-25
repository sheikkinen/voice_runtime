"""RED phase tests for voice_runtime.providers.elevenlabs_tts.

Tests the ElevenLabsTTS provider: speak() pipeline, barge-in interrupt,
disconnected session handling. Uses mocked ElevenLabs client.

NC-152 Phase 2, Step 2.
NC-159: Removed ffmpeg mocking — native ulaw_8000 output format.
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
    session.schedule_twilio_clear = MagicMock()
    session.tap_agent = MagicMock()
    return session


class TestElevenLabsTTSSpeak:
    def test_empty_text_returns_empty(self):
        from voice_runtime.providers.elevenlabs_tts import ElevenLabsTTS

        tts = ElevenLabsTTS()
        session = _make_session()
        result = tts.speak("", session)
        assert result["last_spoken"] == ""

    def test_disconnected_session_returns_early(self):
        from voice_runtime.providers.elevenlabs_tts import ElevenLabsTTS

        tts = ElevenLabsTTS()
        session = _make_session(disconnected=True)
        result = tts.speak("hello", session)
        assert result.get("call_disconnected") is True
        assert result["last_spoken"] == ""

    def test_speak_streams_native_mulaw(self):
        """NC-159: speak() requests ulaw_8000 and streams directly (no ffmpeg)."""
        from voice_runtime.providers.elevenlabs_tts import ElevenLabsTTS

        tts = ElevenLabsTTS()
        session = _make_session()

        mock_client = MagicMock()
        mulaw_chunks = [b"\x00" * 320, b"\xff" * 160]
        mock_client.text_to_speech.convert.return_value = iter(mulaw_chunks)

        with patch("elevenlabs.ElevenLabs", return_value=mock_client):
            result = tts.speak("hello world", session)

        assert result["last_spoken"] == "hello world"
        # Verify ulaw_8000 format requested
        call_kwargs = mock_client.text_to_speech.convert.call_args[1]
        assert call_kwargs["output_format"] == "ulaw_8000"
        # Audio streamed directly to session
        assert session.put_outbound_sync.call_count == 2
        session.tap_agent.assert_called()
        session.send_mark_and_wait.assert_called_once_with("tts_complete", timeout=30.0)

    def test_barge_in_interrupts_playback(self):
        """NC-159: barge-in returns interrupted=True without ffmpeg terminate."""
        from voice_runtime.providers.elevenlabs_tts import ElevenLabsTTS

        tts = ElevenLabsTTS()
        session = _make_session()

        stop_event = threading.Event()
        stop_event.set()  # pre-set — interrupt immediately on first chunk

        mock_client = MagicMock()
        mock_client.text_to_speech.convert.return_value = iter([b"\x00" * 160, b"\xff" * 160])

        with patch("elevenlabs.ElevenLabs", return_value=mock_client):
            result = tts.speak("hello", session, stop_event=stop_event)

        assert result.get("interrupted") is True
        assert result["last_spoken"] == "hello"

    def test_speak_accepts_voice_id_override(self):
        from voice_runtime.providers.elevenlabs_tts import ElevenLabsTTS

        tts = ElevenLabsTTS(voice_id="custom_voice")
        session = _make_session()

        mock_client = MagicMock()
        mock_client.text_to_speech.convert.return_value = iter([b"\x00" * 160])

        with patch("elevenlabs.ElevenLabs", return_value=mock_client):
            tts.speak("test", session)

        call_kwargs = mock_client.text_to_speech.convert.call_args[1]
        assert call_kwargs["voice_id"] == "custom_voice"

    def test_mark_timeout_does_not_raise(self):
        """Mark timeout logs warning but doesn't raise."""
        from voice_runtime.providers.elevenlabs_tts import ElevenLabsTTS

        tts = ElevenLabsTTS()
        session = _make_session()
        session.send_mark_and_wait.side_effect = TimeoutError("mark timeout")

        mock_client = MagicMock()
        mock_client.text_to_speech.convert.return_value = iter([b"\x00" * 160])

        with patch("elevenlabs.ElevenLabs", return_value=mock_client):
            result = tts.speak("test", session)

        assert result["last_spoken"] == "test"
