"""Transport factory."""

from __future__ import annotations


def create_transport(provider: str = "twilio", **kwargs):
    """Create transport module reference.

    Args:
        provider: Transport provider name ("twilio").
        **kwargs: Reserved for future transport configuration.

    Returns:
        The transport module (use register_voice_websocket from it).
    """
    if provider == "twilio":
        from voice_runtime.transports import twilio_ws

        return twilio_ws
    raise ValueError(f"Unknown transport: {provider}")
