"""Twilio REST client construction — the single bounded-timeout boundary.

VR-002 (issue #2): the Twilio SDK's default HTTP client applies no request
timeout, so a SYN-blackhole egress blocks a synchronous call-path request for
the OS TCP timeout (~2 min). Every Twilio REST client in this package is built
here so no call site can be unbounded.
"""

from __future__ import annotations

import math
import os

from ._twilio_models import TwilioRegion

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


def build_explicit_twilio_client(region: TwilioRegion):
    """Bind explicit credentials and host without reading ambient SDK routing."""
    from twilio.http.http_client import TwilioHttpClient
    from twilio.rest import Client

    region = TwilioRegion.model_validate(region)
    try:
        timeout = float(os.getenv("TWILIO_HTTP_TIMEOUT", str(DEFAULT_TIMEOUT_S)))
    except ValueError:
        raise ValueError("TWILIO_HTTP_TIMEOUT must be finite and positive") from None
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("TWILIO_HTTP_TIMEOUT must be finite and positive")
    client = Client(
        *region.auth,
        account_sid=region.account_sid,
        # The SDK replaces an EMPTY environment mapping with os.environ.
        environment={"TWILIO_REGION": "", "TWILIO_EDGE": ""},
        http_client=TwilioHttpClient(timeout=timeout),
    )
    client.region = client.edge = None
    client.api.base_url = f"https://{region.api_host}"
    return client
