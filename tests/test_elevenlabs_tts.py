"""RED phase tests for voice_runtime.providers.elevenlabs_tts.

Tests the ElevenLabsTTS provider: speak() pipeline, barge-in interrupt,
disconnected session handling. Uses mocked ElevenLabs client and ffmpeg.

NC-152 Phase 2, Step 2.
"""

from __future__ import annotations

import subprocess
import threading
from unittest.mock import MagicMock, patch

import pytest


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

    def test_speak_calls_elevenlabs_and_ffmpeg(self):
        from voice_runtime.providers.elevenlabs_tts import ElevenLabsTTS

        tts = ElevenLabsTTS()
        session = _make_session()

        # Mock ffmpeg process
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        # Return some mulaw chunks then EOF
        mock_proc.stdout.read = MagicMock(side_effect=[b"\x00" * 640, b""])
        mock_proc.wait = MagicMock()

        # Mock ElevenLabs client
        mock_client = MagicMock()
        mock_client.text_to_speech.convert.return_value = iter([b"mp3data"])

        with patch("subprocess.Popen", return_value=mock_proc), \
             patch("elevenlabs.ElevenLabs", return_value=mock_client):
            result = tts.speak("hello world", session)

        assert result["last_spoken"] == "hello world"
        session.put_outbound_sync.assert_called()
        session.send_mark_and_wait.assert_called_once_with("tts_complete", timeout=30.0)

    def test_barge_in_interrupts_playback(self):
        from voice_runtime.providers.elevenlabs_tts import ElevenLabsTTS

        tts = ElevenLabsTTS()
        session = _make_session()

        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        # Simulate chunks with stop_event set before second read
        stop_event = threading.Event()
        stop_event.set()  # pre-set — interrupt immediately on first chunk
        mock_proc.stdout.read = MagicMock(return_value=b"\x00" * 160)
        mock_proc.terminate = MagicMock()

        mock_client = MagicMock()
        mock_client.text_to_speech.convert.return_value = iter([b"mp3"])

        with patch("subprocess.Popen", return_value=mock_proc), \
             patch("elevenlabs.ElevenLabs", return_value=mock_client):
            result = tts.speak("hello", session, stop_event=stop_event)

        assert result.get("interrupted") is True
        mock_proc.terminate.assert_called()

    def test_speak_accepts_voice_id_override(self):
        from voice_runtime.providers.elevenlabs_tts import ElevenLabsTTS

        tts = ElevenLabsTTS(voice_id="custom_voice")
        session = _make_session()

        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stdout.read = MagicMock(side_effect=[b"\x00" * 640, b""])
        mock_proc.wait = MagicMock()

        mock_client = MagicMock()
        mock_client.text_to_speech.convert.return_value = iter([b"mp3"])

        with patch("subprocess.Popen", return_value=mock_proc), \
             patch("elevenlabs.ElevenLabs", return_value=mock_client):
            tts.speak("test", session)

        call_kwargs = mock_client.text_to_speech.convert.call_args[1]
        assert call_kwargs["voice_id"] == "custom_voice"

    def test_mark_timeout_does_not_raise(self):
        """Mark timeout logs warning but doesn't raise."""
        from voice_runtime.providers.elevenlabs_tts import ElevenLabsTTS

        tts = ElevenLabsTTS()
        session = _make_session()
        session.send_mark_and_wait.side_effect = TimeoutError("mark timeout")

        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stdout.read = MagicMock(side_effect=[b"\x00" * 640, b""])
        mock_proc.wait = MagicMock()

        mock_client = MagicMock()
        mock_client.text_to_speech.convert.return_value = iter([b"mp3"])

        with patch("subprocess.Popen", return_value=mock_proc), \
             patch("elevenlabs.ElevenLabs", return_value=mock_client):
            result = tts.speak("test", session)

        assert result["last_spoken"] == "test"
