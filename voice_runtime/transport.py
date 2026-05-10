"""Transport and SMS factory utilities.

For the Twilio WebSocket transport, import directly:

    from voice_runtime.transports.twilio_ws import register_voice_websocket
    from voice_runtime.transports.twilio_call import initiate_outbound_call, build_stream_twiml

Use get_sms_transport() for SMS delivery.
"""

from __future__ import annotations


def get_sms_transport(provider: str = "twilio"):
    """Return SMS transport module for the given provider."""
    if provider == "twilio":
        from voice_runtime.transports import twilio_sms

        return twilio_sms
    raise ValueError(f"Unknown SMS transport: {provider}")
