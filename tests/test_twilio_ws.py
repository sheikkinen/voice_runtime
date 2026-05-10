"""RED phase tests for voice_runtime.transports.twilio_ws.

Tests the Twilio Media Streams WebSocket handler: event dispatch,
audio routing, mark sync, lifecycle signals, and Twilio signature validation.

NC-152 Phase 2, Step 3.
NC-283: Twilio request signature validation.
"""

from __future__ import annotations

import asyncio
import base64
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from twilio.request_validator import RequestValidator


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
    # NC-154: intent API fields — None means "not configured"
    session._disconnect_requested = None
    session._clear_queue = None
    session.stt_factory = None
    session.stt = None
    return session


class TestTwilioTransportRegistration:
    def test_register_creates_websocket_route(self):
        from voice_runtime.transports.twilio_ws import register_voice_websocket

        app = FastAPI()
        session = _make_session()
        register_voice_websocket(app, session)
        # Verify route exists
        routes = [r.path for r in app.routes]
        assert "/voice" in routes


class TestTwilioTransportProtocol:
    """Test the Twilio Media Streams protocol handling."""

    def test_connected_event_logged(self):
        from voice_runtime.transports.twilio_ws import register_voice_websocket

        app = FastAPI()
        session = _make_session()
        register_voice_websocket(app, session)

        with TestClient(app) as client:
            with client.websocket_connect("/voice") as ws:
                ws.send_text(json.dumps({"event": "connected"}))
                ws.send_text(json.dumps({"event": "stop"}))

        session.signal_disconnected.assert_called()

    def test_start_event_signals_ws_connected(self):
        from voice_runtime.transports.twilio_ws import register_voice_websocket

        app = FastAPI()
        session = _make_session()
        register_voice_websocket(app, session)

        with TestClient(app) as client:
            with client.websocket_connect("/voice") as ws:
                ws.send_text(
                    json.dumps(
                        {
                            "event": "start",
                            "streamSid": "stream_abc",
                            "start": {"callSid": "call_123"},
                        }
                    )
                )
                ws.send_text(json.dumps({"event": "stop"}))

        session.signal_ws_connected.assert_called_once_with("stream_abc")
        assert session.call_sid == "call_123"

    def test_media_event_puts_decoded_audio_to_inbound(self):
        from voice_runtime.transports.twilio_ws import register_voice_websocket

        app = FastAPI()
        session = _make_session()
        register_voice_websocket(app, session)

        audio_data = b"\xab" * 160
        encoded = base64.b64encode(audio_data).decode()

        with TestClient(app) as client:
            with client.websocket_connect("/voice") as ws:
                ws.send_text(
                    json.dumps(
                        {
                            "event": "media",
                            "media": {"payload": encoded},
                        }
                    )
                )
                ws.send_text(json.dumps({"event": "stop"}))

        session.put_inbound.assert_called_once_with(audio_data)

    def test_mark_event_signals_mark_received(self):
        from voice_runtime.transports.twilio_ws import register_voice_websocket

        app = FastAPI()
        session = _make_session()
        register_voice_websocket(app, session)

        with TestClient(app) as client:
            with client.websocket_connect("/voice") as ws:
                ws.send_text(
                    json.dumps(
                        {
                            "event": "mark",
                            "mark": {"name": "tts_complete"},
                        }
                    )
                )
                ws.send_text(json.dumps({"event": "stop"}))

        session.signal_mark_received.assert_called_once_with("tts_complete")

    def test_stop_event_signals_disconnected(self):
        from voice_runtime.transports.twilio_ws import register_voice_websocket

        app = FastAPI()
        session = _make_session()
        register_voice_websocket(app, session)

        with TestClient(app) as client:
            with client.websocket_connect("/voice") as ws:
                ws.send_text(json.dumps({"event": "stop"}))

        session.signal_disconnected.assert_called_once()

    def test_media_event_taps_caller(self):
        """Audio monitoring: media frames are tapped to session.tap_caller."""
        from voice_runtime.transports.twilio_ws import register_voice_websocket

        app = FastAPI()
        session = _make_session()
        register_voice_websocket(app, session)

        audio_data = b"\xcd" * 160
        encoded = base64.b64encode(audio_data).decode()

        with TestClient(app) as client:
            with client.websocket_connect("/voice") as ws:
                ws.send_text(
                    json.dumps(
                        {
                            "event": "media",
                            "media": {"payload": encoded},
                        }
                    )
                )
                ws.send_text(json.dumps({"event": "stop"}))

        session.tap_caller.assert_called_once_with(audio_data)


# ---------------------------------------------------------------------------
# NC-283: Twilio signature validation
# ---------------------------------------------------------------------------

_TEST_TOKEN = "test_auth_token_nc283"
_TEST_STREAM_URL = "https://example.ngrok.io"
_TEST_WS_URL = "wss://example.ngrok.io/voice"


def _valid_signature(url: str = _TEST_WS_URL, token: str = _TEST_TOKEN) -> str:
    """Compute a valid X-Twilio-Signature for the given URL and token."""
    return RequestValidator(token).compute_signature(url, {})


@pytest.mark.req("NC-283")
class TestSignatureValidation:
    """NC-283: Twilio signature validation on WebSocket upgrade."""

    def test_invalid_signature_closes_with_1008(self, monkeypatch):
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", _TEST_TOKEN)
        monkeypatch.setenv("VOICE_STREAM_URL", _TEST_STREAM_URL)
        monkeypatch.delenv("TWILIO_SKIP_SIGNATURE_VALIDATION", raising=False)

        from voice_runtime.transports import twilio_ws

        monkeypatch.setattr(twilio_ws, "_SKIP_VALIDATION", False)

        from voice_runtime.transports.twilio_ws import register_voice_websocket

        app = FastAPI()
        session = _make_session()
        register_voice_websocket(app, session)

        with TestClient(app) as client:
            with client.websocket_connect(
                "/voice",
                headers={"X-Twilio-Signature": "invalid_signature"},
            ) as ws:
                with pytest.raises(WebSocketDisconnect) as exc_info:
                    ws.receive_text()
            assert exc_info.value.code == 1008

    def test_valid_signature_is_accepted(self, monkeypatch):
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", _TEST_TOKEN)
        monkeypatch.setenv("VOICE_STREAM_URL", _TEST_STREAM_URL)
        monkeypatch.delenv("TWILIO_SKIP_SIGNATURE_VALIDATION", raising=False)

        from voice_runtime.transports import twilio_ws

        monkeypatch.setattr(twilio_ws, "_SKIP_VALIDATION", False)

        from voice_runtime.transports.twilio_ws import register_voice_websocket

        app = FastAPI()
        session = _make_session()
        register_voice_websocket(app, session)

        sig = _valid_signature()
        with TestClient(app) as client:
            with client.websocket_connect(
                "/voice",
                headers={"X-Twilio-Signature": sig},
            ) as ws:
                ws.send_text(json.dumps({"event": "stop"}))

        session.signal_disconnected.assert_called()

    def test_missing_auth_token_skips_validation(self, monkeypatch):
        """When TWILIO_AUTH_TOKEN is not set, validation is bypassed with a warning."""
        monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
        monkeypatch.setenv("VOICE_STREAM_URL", _TEST_STREAM_URL)
        monkeypatch.delenv("TWILIO_SKIP_SIGNATURE_VALIDATION", raising=False)

        from voice_runtime.transports import twilio_ws

        monkeypatch.setattr(twilio_ws, "_SKIP_VALIDATION", False)

        from voice_runtime.transports.twilio_ws import register_voice_websocket

        app = FastAPI()
        session = _make_session()
        register_voice_websocket(app, session)

        with TestClient(app) as client:
            with client.websocket_connect("/voice") as ws:
                ws.send_text(json.dumps({"event": "stop"}))

        session.signal_disconnected.assert_called()

    def test_skip_validation_env_bypasses_check(self, monkeypatch):
        """TWILIO_SKIP_SIGNATURE_VALIDATION=1 allows connection with no signature."""
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", _TEST_TOKEN)
        monkeypatch.setenv("VOICE_STREAM_URL", _TEST_STREAM_URL)

        from voice_runtime.transports import twilio_ws

        monkeypatch.setattr(twilio_ws, "_SKIP_VALIDATION", True)

        from voice_runtime.transports.twilio_ws import register_voice_websocket

        app = FastAPI()
        session = _make_session()
        register_voice_websocket(app, session)

        with TestClient(app) as client:
            with client.websocket_connect("/voice") as ws:
                ws.send_text(json.dumps({"event": "stop"}))

        session.signal_disconnected.assert_called()
