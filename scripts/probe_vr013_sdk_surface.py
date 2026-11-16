#!/usr/bin/env python3
"""VR-013 AC-10: offline probe of the Google SDK surface the providers use.

Imports the installed ``google-cloud-speech`` and ``google-cloud-texttospeech``
packages, prints their versions, and checks every class, field and enum
member that ``voice_runtime.providers.google_stt`` and ``google_tts`` rely
on. Makes no network request. Exits 1 if anything is missing.

Usage:
    python scripts/probe_vr013_sdk_surface.py
"""

from __future__ import annotations

import importlib
import importlib.metadata as metadata
import platform
import sys

NAMES = [
    "google.api_core.client_options.ClientOptions",
    "google.api_core.exceptions.ServiceUnavailable",
    "google.api_core.exceptions.DeadlineExceeded",
    "google.api_core.exceptions.InternalServerError",
    "google.api_core.exceptions.Aborted",
    "google.api_core.exceptions.PermissionDenied",
    "google.api_core.exceptions.Unauthenticated",
    "google.api_core.exceptions.InvalidArgument",
    "google.api_core.exceptions.NotFound",
    "google.api_core.exceptions.ResourceExhausted",
    "google.api_core.exceptions.GoogleAPICallError",
    "google.cloud.speech_v2.SpeechAsyncClient",
    "google.cloud.speech_v2.types.cloud_speech.StreamingRecognizeRequest",
    "google.cloud.speech_v2.types.cloud_speech.StreamingRecognizeResponse",
    "google.cloud.speech_v2.types.cloud_speech.StreamingRecognitionConfig",
    "google.cloud.speech_v2.types.cloud_speech.StreamingRecognitionFeatures",
    "google.cloud.speech_v2.types.cloud_speech.StreamingRecognitionResult",
    "google.cloud.speech_v2.types.cloud_speech.RecognitionConfig",
    "google.cloud.speech_v2.types.cloud_speech.ExplicitDecodingConfig",
    "google.cloud.speech_v2.types.cloud_speech.SpeechAdaptation",
    "google.cloud.speech_v2.types.cloud_speech.SpeechAdaptation.AdaptationPhraseSet",
    "google.cloud.speech_v2.types.cloud_speech.PhraseSet",
    "google.cloud.speech_v2.types.cloud_speech.PhraseSet.Phrase",
    "google.cloud.texttospeech.TextToSpeechClient",
    "google.cloud.texttospeech.StreamingSynthesizeRequest",
    "google.cloud.texttospeech.StreamingSynthesizeConfig",
    "google.cloud.texttospeech.StreamingAudioConfig",
    "google.cloud.texttospeech.StreamingSynthesisInput",
    "google.cloud.texttospeech.VoiceSelectionParams",
    "google.cloud.texttospeech.AudioEncoding",
]

ENUM_MEMBERS = {
    "google.cloud.speech_v2.types.cloud_speech.ExplicitDecodingConfig.AudioEncoding": [
        "MULAW"
    ],
    "google.cloud.speech_v2.types.cloud_speech.StreamingRecognitionFeatures.EndpointingSensitivity": [
        "ENDPOINTING_SENSITIVITY_SUPERSHORT",
        "ENDPOINTING_SENSITIVITY_SHORT",
        "ENDPOINTING_SENSITIVITY_STANDARD",
    ],
    "google.cloud.speech_v2.types.cloud_speech.StreamingRecognizeResponse.SpeechEventType": [
        "SPEECH_ACTIVITY_BEGIN",
        "SPEECH_ACTIVITY_END",
    ],
    "google.cloud.texttospeech.AudioEncoding": ["MULAW"],
}

FIELDS = {
    "google.cloud.speech_v2.types.cloud_speech.StreamingRecognitionFeatures": [
        "interim_results",
        "enable_voice_activity_events",
        "endpointing_sensitivity",
    ],
    "google.cloud.speech_v2.types.cloud_speech.RecognitionConfig": [
        "explicit_decoding_config",
        "model",
        "language_codes",
        "adaptation",
    ],
    "google.cloud.texttospeech.StreamingAudioConfig": [
        "audio_encoding",
        "sample_rate_hertz",
    ],
}


def resolve(dotted: str):
    parts = dotted.split(".")
    for split in range(len(parts), 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:split]))
        except ImportError:
            continue
        for attr in parts[split:]:
            obj = getattr(obj, attr)
        return obj
    raise ImportError(dotted)


def main() -> int:
    print(f"python {platform.python_version()}")
    for dist in ("google-cloud-speech", "google-cloud-texttospeech", "google-api-core"):
        print(f"{dist} {metadata.version(dist)}")
    missing: list[str] = []
    for name in NAMES:
        try:
            resolve(name)
        except (ImportError, AttributeError):
            missing.append(name)
    for name, members in ENUM_MEMBERS.items():
        try:
            enum = resolve(name)
        except (ImportError, AttributeError):
            missing.append(name)
            continue
        missing.extend(f"{name}.{m}" for m in members if not hasattr(enum, m))
    for name, fields in FIELDS.items():
        try:
            known = set(resolve(name).meta.fields)
        except (ImportError, AttributeError):
            missing.append(name)
            continue
        missing.extend(f"{name}.{f}" for f in fields if f not in known)
    checked = (
        len(NAMES)
        + sum(map(len, ENUM_MEMBERS.values()))
        + sum(map(len, FIELDS.values()))
    )
    if missing:
        print(f"MISSING {len(missing)}/{checked}:")
        for name in missing:
            print(f"  {name}")
        return 1
    print(f"OK {checked} names present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
