"""TTS provider factory. Mirrors yamlgraph create_llm() pattern."""

from __future__ import annotations


def create_tts(provider: str = "elevenlabs", **kwargs):
    """Create TTS provider instance.

    Args:
        provider: TTS provider name ("elevenlabs").
        **kwargs: Passed to provider constructor (api_key, voice_id, model_id).
    """
    if provider == "elevenlabs":
        from voice_runtime.providers.elevenlabs_tts import ElevenLabsTTS

        return ElevenLabsTTS(**kwargs)
    elif provider == "azure":
        from voice_runtime.providers.azure_tts import AzureTTS

        return AzureTTS(**kwargs)
    raise ValueError(f"Unknown TTS provider: {provider}")
