"""RED phase tests for voice_runtime.transports.twilio_call.

Tests TwiML generation, URL conversion, and missing env var handling.

NC-155: TDD process correction — these tests should have existed
when twilio_call.py was extracted in NC-154.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.req("NC-155")
class TestBuildStreamTwiml:
    def test_twiml_contains_stream_element(self):
        from voice_runtime.transports.twilio_call import build_stream_twiml

        xml = build_stream_twiml("https://example.ngrok.io")
        assert "<Stream" in xml
        assert "<Response>" in xml
        assert "<Connect>" in xml

    def test_https_converted_to_wss(self):
        from voice_runtime.transports.twilio_call import build_stream_twiml

        xml = build_stream_twiml("https://example.ngrok.io")
        assert "wss://example.ngrok.io/voice" in xml
        assert "https://" not in xml

    def test_http_converted_to_ws(self):
        from voice_runtime.transports.twilio_call import build_stream_twiml

        xml = build_stream_twiml("http://localhost:8080")
        assert "ws://localhost:8080/voice" in xml

    def test_build_stream_xml_is_alias(self):
        from voice_runtime.transports.twilio_call import (
            build_stream_twiml,
            build_stream_xml,
        )

        assert build_stream_xml is build_stream_twiml


@pytest.mark.req("NC-155")
class TestInitiateOutboundCall:
    def test_missing_stream_url_raises(self):
        from voice_runtime.transports.twilio_call import initiate_outbound_call

        with patch.dict(
            "os.environ",
            {"TWILIO_ACCOUNT_SID": "AC1", "TWILIO_AUTH_TOKEN": "t", "TWILIO_PHONE_NUMBER": "+1", "VOICE_STREAM_URL": ""},
            clear=False,
        ):
            # Also clear NGROK_URL fallback
            with patch.dict("os.environ", {"NGROK_URL": ""}, clear=False):
                with pytest.raises(RuntimeError, match="VOICE_STREAM_URL"):
                    initiate_outbound_call("+358401234567")

    def test_missing_credentials_raises(self):
        from voice_runtime.transports.twilio_call import initiate_outbound_call

        with patch.dict(
            "os.environ",
            {"TWILIO_ACCOUNT_SID": "", "TWILIO_AUTH_TOKEN": "", "VOICE_STREAM_URL": "https://x"},
            clear=False,
        ):
            with pytest.raises(RuntimeError, match="TWILIO_ACCOUNT_SID"):
                initiate_outbound_call("+358401234567")

    def test_missing_phone_number_raises(self):
        from voice_runtime.transports.twilio_call import initiate_outbound_call

        with patch.dict(
            "os.environ",
            {
                "TWILIO_ACCOUNT_SID": "AC1",
                "TWILIO_AUTH_TOKEN": "t",
                "TWILIO_PHONE_NUMBER": "",
                "VOICE_STREAM_URL": "https://x",
            },
            clear=False,
        ):
            with pytest.raises(RuntimeError, match="TWILIO_PHONE_NUMBER"):
                initiate_outbound_call("+358401234567")

    def test_successful_call_returns_sid(self):
        from voice_runtime.transports.twilio_call import initiate_outbound_call

        mock_call = MagicMock()
        mock_call.sid = "CA123"
        mock_client = MagicMock()
        mock_client.calls.create.return_value = mock_call

        with patch.dict(
            "os.environ",
            {
                "TWILIO_ACCOUNT_SID": "AC1",
                "TWILIO_AUTH_TOKEN": "token",
                "TWILIO_PHONE_NUMBER": "+15551234567",
                "VOICE_STREAM_URL": "https://example.ngrok.io",
            },
            clear=False,
        ):
            with patch("twilio.rest.Client", return_value=mock_client):
                sid = initiate_outbound_call("+358401234567")

        assert sid == "CA123"
        mock_client.calls.create.assert_called_once()
