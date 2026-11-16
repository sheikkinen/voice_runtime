"""TTS provider factory. Mirrors yamlgraph create_llm() pattern."""

from __future__ import annotations


def create_tts(provider: str = "elevenlabs", **kwargs):
    """Create TTS provider instance.

    Args:
        provider: TTS provider name ("elevenlabs", "azure", "google", "mock").
        **kwargs: Passed to provider constructor (e.g. api_key, voice_id,
            model_id for ElevenLabs; project, location, voice_name for Google).
    """
    if provider == "elevenlabs":
        from voice_runtime.providers.elevenlabs_tts import ElevenLabsTTS

        return ElevenLabsTTS(**kwargs)
    elif provider == "azure":
        from voice_runtime.providers.azure_tts import AzureTTS

        return AzureTTS(**kwargs)
    elif provider == "google":
        from voice_runtime.providers.google_tts import GoogleTTS

        return GoogleTTS(**kwargs)
    elif provider == "mock":
        from voice_runtime.mock.tts import MockTts

        return MockTts(**kwargs)
    raise ValueError(f"Unknown TTS provider: {provider}")
