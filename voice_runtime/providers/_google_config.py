"""Configuration shared by the Google Chirp 3 providers (VR-013 R-4).

Precedence: an explicit non-``None`` argument wins over the environment, and
the environment wins over the default. Only the STT request builder imports
the Speech SDK, so ``google_tts`` does not load it.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

DEFAULT_LOCATION = "eu"
DEFAULT_MODEL = "chirp_3"
DEFAULT_LANGUAGE = "fi-FI"
DEFAULT_ENDPOINTING = "supershort"
SAMPLE_RATE_HZ = 8000
ENDPOINTING_NAMES = {
    "supershort": "ENDPOINTING_SENSITIVITY_SUPERSHORT",
    "short": "ENDPOINTING_SENSITIVITY_SHORT",
    "standard": "ENDPOINTING_SENSITIVITY_STANDARD",
}


def snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def env_or(explicit: str | None, variable: str, default: str) -> str:
    if explicit is not None:
        return explicit
    return os.getenv(variable) or default


def resolve_project(explicit: str | None, owner: str) -> str | None:
    """Explicit project, else the first non-empty project variable, else None."""
    if explicit is not None:
        if not explicit:
            raise ValueError(f"{owner}: explicit project must not be empty")
        return explicit
    return (
        os.getenv("GOOGLE_SPEECH_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT") or None
    )


def parse_hints(raw: Sequence[str] | None) -> tuple[str, ...]:
    """Normalise phrase hints: strip, drop empty, keep order."""
    if raw is None:
        env = os.getenv("GOOGLE_STT_PHRASES")
        items: list[Any] = env.split(",") if env else []
    elif isinstance(raw, str):
        raise ValueError("phrase_hints must be a sequence of strings, not a str")
    else:
        items = list(raw)
    hints: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise ValueError(f"phrase hint {item!r} is not a string")
        hint = item.strip()
        if hint:
            hints.append(hint)
    return tuple(hints)


@dataclass(frozen=True)
class SttSettings:
    project: str | None
    location: str
    model: str
    language_code: str
    phrase_hints: tuple[str, ...]
    endpointing: str

    @classmethod
    def resolve(
        cls,
        project: str | None,
        location: str | None,
        model: str | None,
        language_code: str | None,
        phrase_hints: Sequence[str] | None,
        endpointing: str | None,
    ) -> SttSettings:
        sensitivity = env_or(endpointing, "GOOGLE_STT_ENDPOINTING", DEFAULT_ENDPOINTING)
        if sensitivity not in ENDPOINTING_NAMES:
            raise ValueError(
                f"GooglePersistentStt: endpointing {sensitivity!r} is not one of "
                f"{', '.join(ENDPOINTING_NAMES)}"
            )
        return cls(
            project=resolve_project(project, "GooglePersistentStt"),
            location=env_or(location, "GOOGLE_SPEECH_LOCATION", DEFAULT_LOCATION),
            model=env_or(model, "GOOGLE_STT_MODEL", DEFAULT_MODEL),
            language_code=env_or(
                language_code, "GOOGLE_STT_LANGUAGE", DEFAULT_LANGUAGE
            ),
            phrase_hints=parse_hints(phrase_hints),
            endpointing=sensitivity,
        )

    def config_request(self) -> Any:
        """The first request of every stream (VR-013 §1 Config)."""
        from google.cloud.speech_v2.types import cloud_speech

        config = cloud_speech.RecognitionConfig(
            explicit_decoding_config=cloud_speech.ExplicitDecodingConfig(
                encoding=cloud_speech.ExplicitDecodingConfig.AudioEncoding.MULAW,
                sample_rate_hertz=SAMPLE_RATE_HZ,
                audio_channel_count=1,
            ),
            model=self.model,
            language_codes=[self.language_code],
        )
        if self.phrase_hints:
            phrases = [
                cloud_speech.PhraseSet.Phrase(value=h) for h in self.phrase_hints
            ]
            config.adaptation = cloud_speech.SpeechAdaptation(
                phrase_sets=[
                    cloud_speech.SpeechAdaptation.AdaptationPhraseSet(
                        inline_phrase_set=cloud_speech.PhraseSet(phrases=phrases)
                    )
                ]
            )
        sensitivity = cloud_speech.StreamingRecognitionFeatures.EndpointingSensitivity
        return cloud_speech.StreamingRecognizeRequest(
            recognizer=f"projects/{self.project}/locations/{self.location}/recognizers/_",
            streaming_config=cloud_speech.StreamingRecognitionConfig(
                config=config,
                streaming_features=cloud_speech.StreamingRecognitionFeatures(
                    interim_results=True,
                    enable_voice_activity_events=True,
                    endpointing_sensitivity=getattr(
                        sensitivity, ENDPOINTING_NAMES[self.endpointing]
                    ),
                ),
            ),
        )
