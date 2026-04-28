"""NC-260 Gap E: Early STT callback wiring via on_stt_ready.

STT callbacks (on_committed, on_recognizing, on_error) must be wired
immediately when STT is created, not deferred until first _on_speak().
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from voice_runtime.session import VoiceSession


@pytest.mark.req("REQ-YG-170")
class TestOnSttReady:
    """VoiceSession must support on_stt_ready callback."""

    def test_session_has_on_stt_ready_attribute(self) -> None:
        """VoiceSession must have on_stt_ready field."""
        session = VoiceSession(call_sid="test")
        assert hasattr(session, "on_stt_ready")
        assert session.on_stt_ready is None

    def test_on_stt_ready_fires_when_set(self) -> None:
        """on_stt_ready should be callable with an SttProvider."""
        session = VoiceSession(call_sid="test")
        callback = MagicMock()
        session.on_stt_ready = callback

        mock_stt = MagicMock()
        session.on_stt_ready(mock_stt)

        callback.assert_called_once_with(mock_stt)
