"""Twilio REST client construction — the single bounded-timeout boundary.

VR-002 (issue #2): the Twilio SDK's default HTTP client applies no request
timeout, so a SYN-blackhole egress blocks a synchronous call-path request for
the OS TCP timeout (~2 min). Every Twilio REST client in this package is built
here so no call site can be unbounded.
"""

from __future__ import annotations

import os

DEFAULT_TIMEOUT_S = 15.0


def build_twilio_client(account_sid: str, auth_token: str):
    """Twilio REST client whose requests are bounded by TWILIO_HTTP_TIMEOUT."""
    from twilio.http.http_client import TwilioHttpClient
    from twilio.rest import Client

    timeout = float(os.getenv("TWILIO_HTTP_TIMEOUT", str(DEFAULT_TIMEOUT_S)))
    return Client(
        account_sid,
        auth_token,
        http_client=TwilioHttpClient(timeout=timeout),
    )
