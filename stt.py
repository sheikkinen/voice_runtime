"""STT provider factory.

NC-166: Per-turn mode removed. All providers are persistent.
"""

from __future__ import annotations


def create_stt(provider: str = "elevenlabs", **kwargs):
    """Create STT provider instance.

    Args:
        provider: STT provider name ("elevenlabs" or "azure").
        **kwargs: Passed to provider constructor.
    """
    if provider == "elevenlabs":
        from voice_runtime.providers.elevenlabs_stt import PersistentSttSession

        return PersistentSttSession(**kwargs)
    elif provider == "azure":
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        return AzurePersistentStt(**kwargs)
    raise ValueError(f"Unknown STT provider: {provider}")
