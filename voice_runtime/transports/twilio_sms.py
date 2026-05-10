"""Twilio REST SMS transport.

NC-193: Runtime-owned SMS provider integration. Keeps Twilio SDK usage out of
consumer projects (ninchat_voice, outcaller).
"""

from __future__ import annotations

import os


def _get_twilio_env() -> tuple[str, str, str]:
    return (
        os.getenv("TWILIO_ACCOUNT_SID", ""),
        os.getenv("TWILIO_AUTH_TOKEN", ""),
        os.getenv("TWILIO_PHONE_NUMBER", ""),
    )


def send_sms(to: str, body: str) -> dict[str, str]:
    """Send SMS via Twilio and return normalized metadata."""
    from twilio.rest import Client

    account_sid, auth_token, phone_number = _get_twilio_env()
    if not account_sid or not auth_token:
        raise RuntimeError("TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN required")
    if not phone_number:
        raise RuntimeError("TWILIO_PHONE_NUMBER required")

    client = Client(account_sid, auth_token)
    msg = client.messages.create(to=to, from_=phone_number, body=body)
    return {
        "message_sid": str(getattr(msg, "sid", "")),
        "status": str(getattr(msg, "status", "")),
        "to": to,
    }
