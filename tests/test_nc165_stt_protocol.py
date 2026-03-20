"""NC-165: SttProvider Protocol conformance tests.

Verify that all STT providers and SttTee satisfy the SttProvider Protocol.
These tests use pyright/mypy-style structural checking at runtime via
Protocol attribute inspection.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from voice_runtime.providers import SttProvider


# --- Protocol member catalog ---

REQUIRED_METHODS = [
    "set_speaking",
    "arm_barge_in",
    "next_transcript",
    "start",
    "stop",
]

REQUIRED_ATTRIBUTES = [
    "_on_direct_dispatch",
    "_on_direct_transcribed",
]


class TestSttProviderProtocolExists:
    """Verify Protocol is importable and has expected members."""

    def test_protocol_importable(self):
        assert SttProvider is not None

    def test_protocol_has_required_methods(self):
        for method in REQUIRED_METHODS:
            assert hasattr(SttProvider, method), f"SttProvider missing method: {method}"

    def test_protocol_has_required_attributes(self):
        for attr in REQUIRED_ATTRIBUTES:
            annotations = getattr(SttProvider, "__protocol_attrs__", set())
            # Protocol members appear in annotations
            assert attr in SttProvider.__annotations__ or attr in annotations, (
                f"SttProvider missing attribute: {attr}"
            )


class TestAzureSttConformance:
    """Verify AzurePersistentStt satisfies SttProvider."""

    def test_has_all_required_methods(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt.__new__(AzurePersistentStt)
        for method in REQUIRED_METHODS:
            assert callable(getattr(stt, method, None)), (
                f"AzurePersistentStt missing method: {method}"
            )

    def test_has_all_required_attributes(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt()
        for attr in REQUIRED_ATTRIBUTES:
            assert hasattr(stt, attr), (
                f"AzurePersistentStt missing attribute: {attr}"
            )

    def test_attribute_defaults_are_none(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt()
        assert stt._on_direct_dispatch is None
        assert stt._on_direct_transcribed is None


class TestElevenLabsSttConformance:
    """Verify PersistentSttSession satisfies SttProvider."""

    def test_has_all_required_methods(self):
        from voice_runtime.providers.elevenlabs_stt import PersistentSttSession

        stt = PersistentSttSession.__new__(PersistentSttSession)
        for method in REQUIRED_METHODS:
            assert callable(getattr(stt, method, None)), (
                f"PersistentSttSession missing method: {method}"
            )

    def test_has_all_required_attributes(self):
        from voice_runtime.providers.elevenlabs_stt import PersistentSttSession

        stt = PersistentSttSession()
        for attr in REQUIRED_ATTRIBUTES:
            assert hasattr(stt, attr), (
                f"PersistentSttSession missing attribute: {attr}"
            )

    def test_attribute_defaults_are_none(self):
        from voice_runtime.providers.elevenlabs_stt import PersistentSttSession

        stt = PersistentSttSession()
        assert stt._on_direct_dispatch is None
        assert stt._on_direct_transcribed is None


class TestSttTeeConformance:
    """Verify SttTee satisfies SttProvider."""

    def _make_tee(self):
        from voice_runtime.stt_tee import SttTee

        primary = MagicMock()
        secondary = MagicMock()
        return SttTee(primary, secondary)

    def test_has_all_required_methods(self):
        tee = self._make_tee()
        for method in REQUIRED_METHODS:
            assert callable(getattr(tee, method, None)), (
                f"SttTee missing method: {method}"
            )

    def test_has_all_required_attributes(self):
        tee = self._make_tee()
        for attr in REQUIRED_ATTRIBUTES:
            assert hasattr(tee, attr), (
                f"SttTee missing attribute: {attr}"
            )


class TestSessionFieldTypes:
    """Verify VoiceSession uses SttProvider typing (NC-167 absorbed)."""

    def test_stt_field_annotation_is_not_any(self):
        from voice_runtime.session import VoiceSession

        hints = VoiceSession.__dataclass_fields__
        stt_field = hints["stt"]
        # After NC-165, the type should reference SttProvider, not Any
        type_str = str(stt_field.type)
        assert "Any" not in type_str, (
            f"session.stt still typed as Any: {stt_field.type}"
        )

    def test_stt_factory_annotation_is_not_any(self):
        from voice_runtime.session import VoiceSession

        hints = VoiceSession.__dataclass_fields__
        factory_field = hints["stt_factory"]
        type_str = str(factory_field.type)
        assert "Any" not in type_str, (
            f"session.stt_factory still typed as Any: {factory_field.type}"
        )

    def test_stt_secondary_factory_annotation_is_not_any(self):
        from voice_runtime.session import VoiceSession

        hints = VoiceSession.__dataclass_fields__
        factory_field = hints["stt_secondary_factory"]
        type_str = str(factory_field.type)
        assert "Any" not in type_str, (
            f"session.stt_secondary_factory still typed as Any: {factory_field.type}"
        )
