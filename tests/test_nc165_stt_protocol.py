"""NC-165: SttProvider Protocol conformance tests.

Verify that all STT providers and SttTee satisfy the SttProvider Protocol.
NC-166: Protocol simplified to 4 members (on_committed, set_speaking, start, stop).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from voice_runtime.providers import SttProvider


# --- Protocol member catalog (NC-166) ---

REQUIRED_METHODS = [
    "set_speaking",
    "start",
    "stop",
]

REQUIRED_ATTRIBUTES = [
    "on_committed",
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
            assert attr in SttProvider.__annotations__, (
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

    def test_on_committed_default_is_none(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt()
        assert stt.on_committed is None


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

    def test_on_committed_default_is_none(self):
        from voice_runtime.providers.elevenlabs_stt import PersistentSttSession

        stt = PersistentSttSession()
        assert stt.on_committed is None


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

    def test_has_on_committed_property(self):
        tee = self._make_tee()
        # on_committed is a property proxy — verify it exists
        assert hasattr(tee, "on_committed")


class TestSessionFieldTypes:
    """Verify VoiceSession uses SttProvider typing (NC-167 absorbed)."""

    def test_stt_field_annotation_is_not_any(self):
        from voice_runtime.session import VoiceSession

        hints = VoiceSession.__dataclass_fields__
        stt_field = hints["stt"]
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
