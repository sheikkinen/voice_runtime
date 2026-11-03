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


@pytest.mark.req("NC-285")
class TestBuildRouteStreamXml:
    def test_https_converted_to_routed_wss(self):
        from voice_runtime.transports.twilio_call import build_route_stream_xml

        xml = build_route_stream_xml("https://example.ngrok.io", "route-123")

        assert "wss://example.ngrok.io/voice/route-123" in xml
        assert "https://" not in xml

    def test_http_converted_to_routed_ws(self):
        from voice_runtime.transports.twilio_call import build_route_stream_xml

        xml = build_route_stream_xml("http://localhost:8080", "route-123")

        assert "ws://localhost:8080/voice/route-123" in xml

    def test_trailing_slash_does_not_duplicate_separator(self):
        from voice_runtime.transports.twilio_call import build_route_stream_xml

        xml = build_route_stream_xml("https://example.ngrok.io/", "route-123")

        assert "wss://example.ngrok.io/voice/route-123" in xml
        assert "example.ngrok.io//voice" not in xml

    def test_route_token_is_percent_encoded(self):
        from voice_runtime.transports.twilio_call import build_route_stream_xml

        xml = build_route_stream_xml("https://example.ngrok.io", "a/b c?")

        assert "wss://example.ngrok.io/voice/a%2Fb%20c%3F" in xml


@pytest.mark.req("NC-155")
class TestInitiateOutboundCall:
    def test_missing_stream_url_raises(self):
        from voice_runtime.transports.twilio_call import initiate_outbound_call

        with patch.dict(
            "os.environ",
            {
                "TWILIO_ACCOUNT_SID": "AC1",
                "TWILIO_AUTH_TOKEN": "t",
                "TWILIO_PHONE_NUMBER": "+1",
                "VOICE_STREAM_URL": "",
            },
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
            {
                "TWILIO_ACCOUNT_SID": "",
                "TWILIO_AUTH_TOKEN": "",
                "VOICE_STREAM_URL": "https://x",
            },
            clear=False,
        ):
            with pytest.raises(RuntimeError, match="TWILIO_ACCOUNT_SID"):
                initiate_outbound_call("+358401234567")

    def test_missing_phone_number_raises(self):
        from voice_runtime.transports.twilio_call import initiate_outbound_call

        with (
            patch.dict(
                "os.environ",
                {
                    "TWILIO_ACCOUNT_SID": "AC1",
                    "TWILIO_AUTH_TOKEN": "t",
                    "TWILIO_PHONE_NUMBER": "",
                    "VOICE_STREAM_URL": "https://x",
                },
                clear=False,
            ),
            pytest.raises(RuntimeError, match="TWILIO_PHONE_NUMBER"),
        ):
            initiate_outbound_call("+358401234567")

    def test_successful_call_returns_sid(self):
        from voice_runtime.transports.twilio_call import initiate_outbound_call

        mock_call = MagicMock()
        mock_call.sid = "CA123"
        mock_client = MagicMock()
        mock_client.calls.create.return_value = mock_call

        with (
            patch.dict(
                "os.environ",
                {
                    "TWILIO_ACCOUNT_SID": "AC1",
                    "TWILIO_AUTH_TOKEN": "token",
                    "TWILIO_PHONE_NUMBER": "+15551234567",
                    "VOICE_STREAM_URL": "https://example.ngrok.io",
                },
                clear=False,
            ),
            patch("twilio.rest.Client", return_value=mock_client),
        ):
            sid = initiate_outbound_call("+358401234567")

        assert sid == "CA123"
        mock_client.calls.create.assert_called_once()


_REGION_ENV = {
    "TWILIO_ACCOUNT_SID": "",
    "TWILIO_AUTH_TOKEN": "",
    "TWILIO_PHONE_NUMBER": "+15551234567",
    "VOICE_STREAM_URL": "https://example.ngrok.io",
}


def _ie1_region():
    from voice_runtime.transports.twilio_call import TwilioRegion

    return TwilioRegion(
        api_host="api.dublin.ie1.twilio.com",
        account_sid="AC1",
        auth=("SK1", "secret"),
    )


class TestInitiateOutboundCallRegion:
    """An explicit region places the call with its own identity, no Auth Token."""

    def test_region_uses_explicit_client_without_auth_token(self):
        from voice_runtime.transports.twilio_call import initiate_outbound_call

        mock_client = MagicMock()
        mock_client.calls.create.return_value = MagicMock(sid="CA9")
        region = _ie1_region()

        with (
            patch.dict("os.environ", _REGION_ENV, clear=False),
            patch(
                "voice_runtime.transports.twilio_call.build_explicit_twilio_client",
                return_value=mock_client,
            ) as build_explicit,
            patch(
                "voice_runtime.transports.twilio_call.build_twilio_client"
            ) as build_ambient,
        ):
            sid = initiate_outbound_call("+358401234567", region=region)

        assert sid == "CA9"
        build_explicit.assert_called_once_with(region)
        build_ambient.assert_not_called()
        _, kwargs = mock_client.calls.create.call_args
        assert kwargs["to"] == "+358401234567"
        assert kwargs["from_"] == "+15551234567"
        assert 'url="wss://example.ngrok.io/voice"' in kwargs["twiml"]

    def test_region_still_requires_stream_url(self):
        from voice_runtime.transports.twilio_call import initiate_outbound_call

        env = {**_REGION_ENV, "VOICE_STREAM_URL": ""}
        with (
            patch.dict("os.environ", env, clear=False),
            pytest.raises(RuntimeError, match="VOICE_STREAM_URL"),
        ):
            initiate_outbound_call("+358401234567", region=_ie1_region())

    def test_region_still_requires_phone_number(self):
        from voice_runtime.transports.twilio_call import initiate_outbound_call

        env = {**_REGION_ENV, "TWILIO_PHONE_NUMBER": ""}
        with (
            patch.dict("os.environ", env, clear=False),
            pytest.raises(RuntimeError, match="TWILIO_PHONE_NUMBER"),
        ):
            initiate_outbound_call("+358401234567", region=_ie1_region())

    def test_region_is_revalidated(self):
        from voice_runtime.transports.twilio_call import initiate_outbound_call

        with (
            patch.dict("os.environ", _REGION_ENV, clear=False),
            pytest.raises(ValueError, match="Invalid Twilio API host"),
        ):
            initiate_outbound_call(
                "+358401234567",
                region={
                    "api_host": "evil.example.com",
                    "account_sid": "AC1",
                    "auth": ("a", "b"),
                },
            )


class TestHangupCall:
    """ninchat_voice NC-362: supervisor-side reap teardown rides this seam."""

    def test_hangup_updates_call_to_completed(self):
        from voice_runtime.transports.twilio_call import hangup_call

        mock_client = MagicMock()
        with (
            patch.dict(
                "os.environ",
                {"TWILIO_ACCOUNT_SID": "AC1", "TWILIO_AUTH_TOKEN": "token"},
                clear=False,
            ),
            patch("twilio.rest.Client", return_value=mock_client),
        ):
            hangup_call("CA-reaped")

        mock_client.calls.assert_called_once_with("CA-reaped")
        mock_client.calls.return_value.update.assert_called_once_with(
            status="completed"
        )

    def test_hangup_without_credentials_raises(self):
        from voice_runtime.transports.twilio_call import hangup_call

        with (
            patch.dict(
                "os.environ",
                {"TWILIO_ACCOUNT_SID": "", "TWILIO_AUTH_TOKEN": ""},
                clear=False,
            ),
            pytest.raises(RuntimeError, match="TWILIO_ACCOUNT_SID"),
        ):
            hangup_call("CA-x")
