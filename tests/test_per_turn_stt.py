"""RED phase tests for voice_runtime.providers.elevenlabs_stt.PerTurnStt.

Tests the per-turn STT listen pipeline: happy path, timeout,
disconnect, no loop, API errors.

NC-155: TDD process correction — PerTurnStt shipped with zero
unit tests in voice_runtime.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_session(loop: asyncio.AbstractEventLoop | None = None) -> MagicMock:
    """Create a mock VoiceSession."""
    session = MagicMock()
    session.loop = loop
    session.is_disconnected = False
    session.inbound = asyncio.Queue()
    return session


@pytest.mark.req("NC-155")
class TestPerTurnSttListen:
    def test_no_loop_returns_empty(self):
        from voice_runtime.providers.elevenlabs_stt import PerTurnStt

        stt = PerTurnStt(api_key="test-key")
        session = _make_session(loop=None)
        result = stt.listen(session, timeout=5)
        assert result == ""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_returns_transcript(self):
        from voice_runtime.providers.elevenlabs_stt import PerTurnStt

        loop = asyncio.get_running_loop()
        session = _make_session(loop=loop)

        # Mock the ElevenLabs client and Scribe connection
        mock_stt = AsyncMock()
        mock_stt.close = AsyncMock()
        mock_stt.send = AsyncMock()

        # Store the on_committed_transcript callback
        callbacks = {}

        def capture_on(event_name, callback):
            callbacks[event_name] = callback

        mock_stt.on = capture_on

        mock_client = MagicMock()
        mock_client.speech_to_text.realtime.connect = AsyncMock(return_value=mock_stt)

        with patch("elevenlabs.ElevenLabs", return_value=mock_client):
            # Put a frame so feed_audio doesn't immediately timeout
            session.inbound.put_nowait(b"\x00" * 160)

            # Schedule triggering the callback after a brief delay
            async def trigger_transcript():
                await asyncio.sleep(0.05)
                callbacks["committed_transcript"]({"text": "Hello world"})

            asyncio.create_task(trigger_transcript())

            result = await loop.run_in_executor(
                None, lambda: PerTurnStt(api_key="test-key").listen(session, timeout=5)
            )

        assert result == "Hello world"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_timeout_returns_empty(self):
        from voice_runtime.providers.elevenlabs_stt import PerTurnStt

        loop = asyncio.get_running_loop()
        session = _make_session(loop=loop)

        mock_stt = AsyncMock()
        mock_stt.close = AsyncMock()
        mock_stt.send = AsyncMock()
        mock_stt.on = MagicMock()

        mock_client = MagicMock()
        mock_client.speech_to_text.realtime.connect = AsyncMock(return_value=mock_stt)

        with patch("elevenlabs.ElevenLabs", return_value=mock_client):
            result = await loop.run_in_executor(
                None, lambda: PerTurnStt(api_key="test-key").listen(session, timeout=0.1)
            )

        assert result == ""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_disconnect_returns_empty(self):
        from voice_runtime.providers.elevenlabs_stt import PerTurnStt

        loop = asyncio.get_running_loop()
        session = _make_session(loop=loop)

        mock_stt = AsyncMock()
        mock_stt.close = AsyncMock()
        mock_stt.send = AsyncMock()
        mock_stt.on = MagicMock()

        mock_client = MagicMock()
        mock_client.speech_to_text.realtime.connect = AsyncMock(return_value=mock_stt)

        # Signal disconnect shortly after starting
        async def disconnect_soon():
            await asyncio.sleep(0.05)
            session.is_disconnected = True

        with patch("elevenlabs.ElevenLabs", return_value=mock_client):
            asyncio.create_task(disconnect_soon())
            result = await loop.run_in_executor(
                None, lambda: PerTurnStt(api_key="test-key").listen(session, timeout=5)
            )

        assert result == ""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_api_error_returns_empty_and_logs(self, caplog):
        from voice_runtime.providers.elevenlabs_stt import PerTurnStt

        loop = asyncio.get_running_loop()
        session = _make_session(loop=loop)

        mock_client = MagicMock()
        mock_client.speech_to_text.realtime.connect = AsyncMock(
            side_effect=ConnectionError("API unavailable")
        )

        with patch("elevenlabs.ElevenLabs", return_value=mock_client):
            with caplog.at_level(logging.ERROR, logger="voice_runtime.providers.elevenlabs_stt"):
                result = await loop.run_in_executor(
                    None, lambda: PerTurnStt(api_key="test-key").listen(session, timeout=1)
                )

        assert result == ""
        assert any("PerTurnStt error" in r.message for r in caplog.records)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_close_called_in_finally(self):
        from voice_runtime.providers.elevenlabs_stt import PerTurnStt

        loop = asyncio.get_running_loop()
        session = _make_session(loop=loop)

        mock_stt = AsyncMock()
        mock_stt.close = AsyncMock()
        mock_stt.send = AsyncMock()
        mock_stt.on = MagicMock()

        mock_client = MagicMock()
        mock_client.speech_to_text.realtime.connect = AsyncMock(return_value=mock_stt)

        with patch("elevenlabs.ElevenLabs", return_value=mock_client):
            await loop.run_in_executor(
                None, lambda: PerTurnStt(api_key="test-key").listen(session, timeout=0.1)
            )

        mock_stt.close.assert_awaited_once()
