"""RED phase tests for STT lifecycle in voice_runtime.transports.twilio_ws.

Tests stt_factory creation, stt.start() call, and stt.stop() in finally.

NC-155: TDD process correction — STT lifecycle in the transport handler
had zero test coverage.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_session():
    """Create a mock VoiceSession with STT support."""
    session = MagicMock()
    session.stream_sid = None
    session.call_sid = None
    session.inbound = asyncio.Queue()
    session.put_inbound = MagicMock()
    session.signal_ws_connected = MagicMock()
    session.signal_disconnected = MagicMock()
    session.signal_mark_received = MagicMock()
    session.get_outbound = AsyncMock(side_effect=asyncio.CancelledError)
    session.get_pending_mark = AsyncMock(side_effect=asyncio.CancelledError)
    session.tap_caller = MagicMock()
    session._disconnect_requested = None
    session._clear_queue = None
    session.stt_factory = None
    session.stt = None
    session.stt_secondary_factory = None
    return session


def _make_app_client(session):
    """Create FastAPI app with voice WS registered and return test client."""
    from voice_runtime.transports.twilio_ws import register_voice_websocket

    app = FastAPI()
    register_voice_websocket(app, session)
    return TestClient(app)


def _send_start_event(ws, stream_sid="SID1", call_sid="CA1"):
    """Send a Twilio 'start' event through the WebSocket."""
    ws.send_text(
        json.dumps(
            {
                "event": "start",
                "streamSid": stream_sid,
                "start": {"callSid": call_sid},
            }
        )
    )


def _send_stop_event(ws):
    """Send a Twilio 'stop' event through the WebSocket."""
    ws.send_text(json.dumps({"event": "stop"}))


@pytest.mark.req("NC-155")
class TestSttLifecycle:
    def test_stt_factory_called_on_start(self):
        session = _make_session()
        mock_stt = AsyncMock()
        mock_stt.start = AsyncMock()
        mock_stt.stop = AsyncMock()
        session.stt_factory = MagicMock(return_value=mock_stt)

        client = _make_app_client(session)
        with client.websocket_connect("/voice") as ws:
            _send_start_event(ws)
            _send_stop_event(ws)

        session.stt_factory.assert_called_once()

    def test_stt_start_awaited(self):
        session = _make_session()
        mock_stt = AsyncMock()
        mock_stt.start = AsyncMock()
        mock_stt.stop = AsyncMock()
        session.stt_factory = MagicMock(return_value=mock_stt)

        client = _make_app_client(session)
        with client.websocket_connect("/voice") as ws:
            _send_start_event(ws)
            _send_stop_event(ws)

        mock_stt.start.assert_awaited_once_with(session.inbound)

    def test_stt_stop_on_disconnect(self):
        session = _make_session()
        mock_stt = AsyncMock()
        mock_stt.start = AsyncMock()
        mock_stt.stop = AsyncMock()
        session.stt_factory = MagicMock(return_value=mock_stt)

        client = _make_app_client(session)
        with client.websocket_connect("/voice") as ws:
            _send_start_event(ws)
            _send_stop_event(ws)

        mock_stt.stop.assert_awaited_once()

    def test_no_stt_when_factory_none(self):
        session = _make_session()
        session.stt_factory = None
        session.stt = None

        client = _make_app_client(session)
        with client.websocket_connect("/voice") as ws:
            _send_start_event(ws)
            _send_stop_event(ws)

        # stt should still be None — no factory, no creation
        assert session.stt is None
