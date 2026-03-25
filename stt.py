"""STT provider factory.

NC-166: Per-turn mode removed. All providers are persistent.
NC-186: Added get_stt_class() for consumers that need the class, not an instance.
"""

from __future__ import annotations


def get_stt_class(provider: str = "elevenlabs"):
    """Return the STT provider class without instantiating.

    Use when a consumer needs the class itself (e.g. as a factory argument).
    """
    if provider == "elevenlabs":
        from voice_runtime.providers.elevenlabs_stt import PersistentSttSession

        return PersistentSttSession
    elif provider == "azure":
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        return AzurePersistentStt
    raise ValueError(f"Unknown STT provider: {provider}")


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
