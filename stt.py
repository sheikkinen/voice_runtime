"""STT provider factory. Supports persistent and per-turn modes."""

from __future__ import annotations


def create_stt(provider: str = "elevenlabs", mode: str = "persistent", **kwargs):
    """Create STT provider instance.

    Args:
        provider: STT provider name ("elevenlabs").
        mode: "persistent" (one WebSocket per call, barge-in, echo discard,
              stability grace) or "per_turn" (new connection per listen() call).
        **kwargs: Passed to provider constructor.
    """
    if provider == "elevenlabs":
        if mode == "persistent":
            from voice_runtime.providers.elevenlabs_stt import (
                PersistentSttSession,
            )

            return PersistentSttSession(**kwargs)
        elif mode == "per_turn":
            from voice_runtime.providers.elevenlabs_stt import PerTurnStt

            return PerTurnStt(**kwargs)
        raise ValueError(f"Unknown STT mode: {mode}")
    raise ValueError(f"Unknown STT provider: {provider}")
