"""Twilio REST call management — outbound initiation and TwiML generation.

NC-154: Extracted from outcaller/nodes/twilio_call.py. All Twilio SDK
usage lives here — consumers never import twilio directly.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import quote

logger = logging.getLogger(__name__)


def _get_twilio_env() -> tuple[str, str, str, str]:
    """Read Twilio environment variables.

    Returns:
        (account_sid, auth_token, phone_number, stream_url)
    """
    return (
        os.getenv("TWILIO_ACCOUNT_SID", ""),
        os.getenv("TWILIO_AUTH_TOKEN", ""),
        os.getenv("TWILIO_PHONE_NUMBER", ""),
        os.getenv("VOICE_STREAM_URL", ""),
    )


def build_stream_twiml(stream_url: str) -> str:
    """Build TwiML XML that connects Twilio to a Media Streams WebSocket.

    Args:
        stream_url: Public URL pointing at the /voice WebSocket endpoint.
            May be https:// or http:// — converted to wss:// or ws://.

    Returns:
        TwiML XML string.
    """
    ws_url = stream_url.replace("https://", "wss://").replace("http://", "ws://")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{ws_url}/voice" />
    </Connect>
</Response>""".strip()


# Alias for consumers that don't want 'twiml' in their import line
build_stream_xml = build_stream_twiml


def build_route_stream_xml(stream_url: str, route_token: str) -> str:
    """Build XML that connects Twilio to an opaque routed stream endpoint.

    Args:
        stream_url: Public base URL. May be https:// or http:// — converted to
            wss:// or ws://.
        route_token: Opaque per-call route token appended under /voice/.

    Returns:
        XML string for Twilio Media Streams.
    """
    ws_base = (
        stream_url.rstrip("/").replace("https://", "wss://").replace("http://", "ws://")
    )
    safe_token = quote(route_token, safe="")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{ws_base}/voice/{safe_token}" />
    </Connect>
</Response>""".strip()


def initiate_outbound_call(phone: str) -> str:
    """Initiate an outbound Twilio call with Media Streams.

    Args:
        phone: Phone number to call (E.164 format).

    Returns:
        call_sid from Twilio.

    Raises:
        RuntimeError: If VOICE_STREAM_URL or Twilio credentials are missing.
    """
    from twilio.rest import Client

    account_sid, auth_token, phone_number, stream_url = _get_twilio_env()

    if not stream_url:
        raise RuntimeError(
            "Set VOICE_STREAM_URL to a public WebSocket URL (use ngrok for local dev)"
        )
    if not account_sid or not auth_token:
        raise RuntimeError("TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN required")
    if not phone_number:
        raise RuntimeError("TWILIO_PHONE_NUMBER required")

    twiml = build_stream_twiml(stream_url)
    logger.info("Initiating outbound call to %s", phone)

    client = Client(account_sid, auth_token)
    call = client.calls.create(
        to=phone,
        from_=phone_number,
        twiml=twiml,
    )
    logger.info("Call initiated: call_sid=%s", call.sid)
    return call.sid
