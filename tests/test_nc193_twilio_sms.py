"""NC-193 RED tests for runtime SMS transport.

Twilio SDK usage must stay in voice_runtime. API contract is minimal and
provider-switch friendly for later EU-capable SMS backend integration.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.req("NC-193")
class TestSmsTransportAccessor:
    def test_get_sms_transport_returns_twilio_module(self):
        from voice_runtime.transport import get_sms_transport

        transport = get_sms_transport("twilio")
        assert hasattr(transport, "send_sms")

    def test_get_sms_transport_unknown_provider_raises(self):
        from voice_runtime.transport import get_sms_transport

        with pytest.raises(ValueError, match="Unknown SMS transport"):
            get_sms_transport("unsupported")


@pytest.mark.req("NC-193")
class TestTwilioSmsTransport:
    def test_missing_credentials_raises(self):
        from voice_runtime.transports.twilio_sms import send_sms

        with patch.dict(
            "os.environ",
            {
                "TWILIO_ACCOUNT_SID": "",
                "TWILIO_AUTH_TOKEN": "",
                "TWILIO_PHONE_NUMBER": "",
            },
            clear=False,
        ):
            with pytest.raises(RuntimeError, match="TWILIO_ACCOUNT_SID"):
                send_sms("+358401234567", "Hei")

    def test_missing_phone_number_raises(self):
        from voice_runtime.transports.twilio_sms import send_sms

        with patch.dict(
            "os.environ",
            {
                "TWILIO_ACCOUNT_SID": "AC123",
                "TWILIO_AUTH_TOKEN": "token",
                "TWILIO_PHONE_NUMBER": "",
            },
            clear=False,
        ):
            with pytest.raises(RuntimeError, match="TWILIO_PHONE_NUMBER"):
                send_sms("+358401234567", "Hei")

    def test_success_returns_normalized_metadata(self):
        from voice_runtime.transports.twilio_sms import send_sms

        mock_msg = MagicMock()
        mock_msg.sid = "SM123"
        mock_msg.status = "queued"

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_msg

        with (
            patch.dict(
                "os.environ",
                {
                    "TWILIO_ACCOUNT_SID": "AC123",
                    "TWILIO_AUTH_TOKEN": "token",
                    "TWILIO_PHONE_NUMBER": "+15551234567",
                },
                clear=False,
            ),
            patch("twilio.rest.Client", return_value=mock_client),
        ):
            result = send_sms("+358401234567", "Hei")

        assert result == {
            "message_sid": "SM123",
            "status": "queued",
            "to": "+358401234567",
        }
        mock_client.messages.create.assert_called_once_with(
            to="+358401234567",
            from_="+15551234567",
            body="Hei",
        )
