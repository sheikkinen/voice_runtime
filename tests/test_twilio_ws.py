"""RED phase tests for voice_runtime.transports.twilio_ws.

Tests the Twilio Media Streams WebSocket handler: event dispatch,
audio routing, mark sync, lifecycle signals.

NC-152 Phase 2, Step 3.
"""

from __future__ import annotations

import asyncio
import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_session():
    """Create a mock VoiceSession for transport testing."""
    session = MagicMock()
    session.stream_sid = None
    session.call_sid = None
    session.inbound = asyncio.Queue()
    session.outbound = asyncio.Queue()
    session.put_inbound = MagicMock()
    session.signal_ws_connected = MagicMock()
    session.signal_disconnected = MagicMock()
    session.signal_mark_received = MagicMock()
    session.get_outbound = AsyncMock(side_effect=asyncio.CancelledError)
    session.get_pending_mark = AsyncMock(side_effect=asyncio.CancelledError)
    session.tap_caller = MagicMock()
    return session


class TestTwilioTransportRegistration:
    def test_register_creates_websocket_route(self):
        from projects.voice_runtime.transports.twilio_ws import register_voice_websocket

        app = FastAPI()
        session = _make_session()
        register_voice_websocket(app, session)
        # Verify route exists
        routes = [r.path for r in app.routes]
        assert "/voice" in routes


class TestTwilioTransportProtocol:
    """Test the Twilio Media Streams protocol handling."""

    def test_connected_event_logged(self):
        from projects.voice_runtime.transports.twilio_ws import register_voice_websocket

        app = FastAPI()
        session = _make_session()
        register_voice_websocket(app, session)

        with TestClient(app) as client:
            with client.websocket_connect("/voice") as ws:
                ws.send_text(json.dumps({"event": "connected"}))
                ws.send_text(json.dumps({"event": "stop"}))

        session.signal_disconnected.assert_called()

    def test_start_event_signals_ws_connected(self):
        from projects.voice_runtime.transports.twilio_ws import register_voice_websocket

        app = FastAPI()
        session = _make_session()
        register_voice_websocket(app, session)

        with TestClient(app) as client:
            with client.websocket_connect("/voice") as ws:
                ws.send_text(json.dumps({
                    "event": "start",
                    "streamSid": "stream_abc",
                    "start": {"callSid": "call_123"},
                }))
                ws.send_text(json.dumps({"event": "stop"}))

        session.signal_ws_connected.assert_called_once_with("stream_abc")
        assert session.call_sid == "call_123"

    def test_media_event_puts_decoded_audio_to_inbound(self):
        from projects.voice_runtime.transports.twilio_ws import register_voice_websocket

        app = FastAPI()
        session = _make_session()
        register_voice_websocket(app, session)

        audio_data = b"\xab" * 160
        encoded = base64.b64encode(audio_data).decode()

        with TestClient(app) as client:
            with client.websocket_connect("/voice") as ws:
                ws.send_text(json.dumps({
                    "event": "media",
                    "media": {"payload": encoded},
                }))
                ws.send_text(json.dumps({"event": "stop"}))

        session.put_inbound.assert_called_once_with(audio_data)

    def test_mark_event_signals_mark_received(self):
        from projects.voice_runtime.transports.twilio_ws import register_voice_websocket

        app = FastAPI()
        session = _make_session()
        register_voice_websocket(app, session)

        with TestClient(app) as client:
            with client.websocket_connect("/voice") as ws:
                ws.send_text(json.dumps({
                    "event": "mark",
                    "mark": {"name": "tts_complete"},
                }))
                ws.send_text(json.dumps({"event": "stop"}))

        session.signal_mark_received.assert_called_once_with("tts_complete")

    def test_stop_event_signals_disconnected(self):
        from projects.voice_runtime.transports.twilio_ws import register_voice_websocket

        app = FastAPI()
        session = _make_session()
        register_voice_websocket(app, session)

        with TestClient(app) as client:
            with client.websocket_connect("/voice") as ws:
                ws.send_text(json.dumps({"event": "stop"}))

        session.signal_disconnected.assert_called_once()

    def test_media_event_taps_caller(self):
        """Audio monitoring: media frames are tapped to session.tap_caller."""
        from projects.voice_runtime.transports.twilio_ws import register_voice_websocket

        app = FastAPI()
        session = _make_session()
        register_voice_websocket(app, session)

        audio_data = b"\xcd" * 160
        encoded = base64.b64encode(audio_data).decode()

        with TestClient(app) as client:
            with client.websocket_connect("/voice") as ws:
                ws.send_text(json.dumps({
                    "event": "media",
                    "media": {"payload": encoded},
                }))
                ws.send_text(json.dumps({"event": "stop"}))

        session.tap_caller.assert_called_once_with(audio_data)
