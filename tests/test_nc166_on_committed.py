"""NC-166: on_committed callback — RED phase tests.

Move processing decisions from voice_runtime to consumer.
Provider fires on_committed for every committed utterance past echo discard.
Consumer decides routing (queue vs dispatch vs ignore).

Removes: _on_direct_dispatch, _on_direct_transcribed, _direct_sent,
         _listening, arm_barge_in(), _barge_in_event, _on_partial(),
         next_transcript(), _transcript_queue, AzurePerTurnStt, PerTurnStt.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from voice_runtime.providers import SttProvider


# ---------------------------------------------------------------------------
# SttProvider Protocol: new shape
# ---------------------------------------------------------------------------


class TestSttProviderProtocolNC166:
    """Protocol should have exactly 4 members after NC-166."""

    def test_has_on_committed(self):
        assert "on_committed" in SttProvider.__annotations__, (
            "SttProvider missing on_committed attribute"
        )

    def test_has_set_speaking(self):
        assert hasattr(SttProvider, "set_speaking")

    def test_has_start(self):
        assert hasattr(SttProvider, "start")

    def test_has_stop(self):
        assert hasattr(SttProvider, "stop")

    def test_no_arm_barge_in(self):
        assert not hasattr(SttProvider, "arm_barge_in"), (
            "arm_barge_in must be removed from Protocol"
        )

    def test_no_next_transcript(self):
        assert not hasattr(SttProvider, "next_transcript"), (
            "next_transcript must be removed from Protocol"
        )

    def test_no_direct_dispatch(self):
        assert "_on_direct_dispatch" not in getattr(SttProvider, "__annotations__", {}), (
            "_on_direct_dispatch must be removed from Protocol"
        )

    def test_no_direct_transcribed(self):
        assert "_on_direct_transcribed" not in getattr(SttProvider, "__annotations__", {}), (
            "_on_direct_transcribed must be removed from Protocol"
        )


# ---------------------------------------------------------------------------
# AzurePersistentStt: on_committed callback
# ---------------------------------------------------------------------------


class TestAzureOnCommitted:
    """AzurePersistentStt fires on_committed for valid text."""

    def test_has_on_committed_attribute(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        assert hasattr(stt, "on_committed")
        assert stt.on_committed is None

    def test_fires_on_committed_callback(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        received = []
        stt.on_committed = lambda text: received.append(text)

        evt = MagicMock()
        evt.result.text = "hello world"
        stt._on_committed(evt)

        assert received == ["hello world"]

    def test_skips_during_speaking(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        received = []
        stt.on_committed = lambda text: received.append(text)
        stt._speaking = True

        evt = MagicMock()
        evt.result.text = "should drop"
        stt._on_committed(evt)

        assert received == []

    def test_skips_during_echo_discard(self):
        import time

        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        received = []
        stt.on_committed = lambda text: received.append(text)
        stt._discard_until = time.monotonic() + 10.0

        evt = MagicMock()
        evt.result.text = "echo text"
        stt._on_committed(evt)

        assert received == []

    def test_skips_empty_text(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        received = []
        stt.on_committed = lambda text: received.append(text)

        evt = MagicMock()
        evt.result.text = "   "
        stt._on_committed(evt)

        assert received == []

    def test_no_callback_no_error(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        # on_committed is None — should not raise
        evt = MagicMock()
        evt.result.text = "hello"
        stt._on_committed(evt)  # must not raise

    def test_no_direct_dispatch_attribute(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        assert not hasattr(stt, "_on_direct_dispatch"), (
            "_on_direct_dispatch must be removed"
        )

    def test_no_direct_transcribed_attribute(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        assert not hasattr(stt, "_on_direct_transcribed"), (
            "_on_direct_transcribed must be removed"
        )

    def test_no_direct_sent_attribute(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        assert not hasattr(stt, "_direct_sent"), (
            "_direct_sent must be removed"
        )

    def test_no_listening_attribute(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        assert not hasattr(stt, "_listening"), (
            "_listening must be removed"
        )

    def test_no_arm_barge_in_method(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        assert not hasattr(stt, "arm_barge_in"), (
            "arm_barge_in must be removed"
        )

    def test_no_next_transcript_method(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        assert not hasattr(stt, "next_transcript"), (
            "next_transcript must be removed"
        )

    def test_no_transcript_queue(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        assert not hasattr(stt, "_transcript_queue"), (
            "_transcript_queue must be removed"
        )

    def test_no_on_partial_method(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        assert not hasattr(stt, "_on_partial"), (
            "_on_partial must be removed"
        )

    def test_no_barge_in_event(self):
        from voice_runtime.providers.azure_stt import AzurePersistentStt

        stt = AzurePersistentStt(subscription_key="test-key")
        assert not hasattr(stt, "_barge_in_event"), (
            "_barge_in_event must be removed"
        )


# ---------------------------------------------------------------------------
# ElevenLabs PersistentSttSession: on_committed callback
# ---------------------------------------------------------------------------


class TestElevenLabsOnCommitted:
    """PersistentSttSession fires on_committed for valid text."""

    def test_has_on_committed_attribute(self):
        from voice_runtime.providers.elevenlabs_stt import PersistentSttSession

        stt = PersistentSttSession(api_key="test-key")
        assert hasattr(stt, "on_committed")
        assert stt.on_committed is None

    def test_fires_on_committed_callback(self):
        from voice_runtime.providers.elevenlabs_stt import PersistentSttSession

        stt = PersistentSttSession(api_key="test-key")
        received = []
        stt.on_committed = lambda text: received.append(text)

        data = {"text": "hello world"}
        stt._on_committed(data)

        assert received == ["hello world"]

    def test_skips_during_speaking(self):
        from voice_runtime.providers.elevenlabs_stt import PersistentSttSession

        stt = PersistentSttSession(api_key="test-key")
        received = []
        stt.on_committed = lambda text: received.append(text)
        stt._speaking = True

        data = {"text": "should drop"}
        stt._on_committed(data)

        assert received == []

    def test_no_direct_dispatch_attribute(self):
        from voice_runtime.providers.elevenlabs_stt import PersistentSttSession

        stt = PersistentSttSession(api_key="test-key")
        assert not hasattr(stt, "_on_direct_dispatch")

    def test_no_arm_barge_in_method(self):
        from voice_runtime.providers.elevenlabs_stt import PersistentSttSession

        stt = PersistentSttSession(api_key="test-key")
        assert not hasattr(stt, "arm_barge_in")

    def test_no_next_transcript_method(self):
        from voice_runtime.providers.elevenlabs_stt import PersistentSttSession

        stt = PersistentSttSession(api_key="test-key")
        assert not hasattr(stt, "next_transcript")

    def test_no_on_partial_method(self):
        from voice_runtime.providers.elevenlabs_stt import PersistentSttSession

        stt = PersistentSttSession(api_key="test-key")
        assert not hasattr(stt, "_on_partial")


# ---------------------------------------------------------------------------
# SttTee: on_committed relay
# ---------------------------------------------------------------------------


class TestSttTeeOnCommitted:
    """SttTee relays on_committed to primary only."""

    def test_proxies_on_committed_to_primary(self):
        from voice_runtime.stt_tee import SttTee

        primary = MagicMock()
        secondary = MagicMock()
        tee = SttTee(primary, secondary)
        cb = MagicMock()
        tee.on_committed = cb
        assert primary.on_committed is cb

    def test_reads_on_committed_from_primary(self):
        from voice_runtime.stt_tee import SttTee

        primary = MagicMock()
        cb = MagicMock()
        primary.on_committed = cb
        secondary = MagicMock()
        tee = SttTee(primary, secondary)
        assert tee.on_committed is cb

    def test_no_direct_dispatch_proxy(self):
        from voice_runtime.stt_tee import SttTee

        primary = MagicMock()
        secondary = MagicMock()
        tee = SttTee(primary, secondary)
        assert not hasattr(tee, "_on_direct_dispatch")

    def test_no_arm_barge_in(self):
        from voice_runtime.stt_tee import SttTee

        primary = MagicMock()
        secondary = MagicMock()
        tee = SttTee(primary, secondary)
        assert not hasattr(tee, "arm_barge_in")

    def test_no_next_transcript(self):
        from voice_runtime.stt_tee import SttTee

        primary = MagicMock()
        secondary = MagicMock()
        tee = SttTee(primary, secondary)
        assert not hasattr(tee, "next_transcript")

    def test_no_transcript_queue(self):
        from voice_runtime.stt_tee import SttTee

        primary = MagicMock()
        secondary = MagicMock()
        tee = SttTee(primary, secondary)
        assert not hasattr(tee, "_transcript_queue")

    def test_no_listening_proxy(self):
        from voice_runtime.stt_tee import SttTee

        primary = MagicMock()
        secondary = MagicMock()
        tee = SttTee(primary, secondary)
        assert not hasattr(tee, "_listening")


# ---------------------------------------------------------------------------
# PerTurnStt classes deleted
# ---------------------------------------------------------------------------


class TestPerTurnSttDeleted:
    """AzurePerTurnStt and PerTurnStt must not exist."""

    def test_no_azure_per_turn_stt(self):
        from voice_runtime.providers import azure_stt

        assert not hasattr(azure_stt, "AzurePerTurnStt"), (
            "AzurePerTurnStt must be deleted"
        )

    def test_no_elevenlabs_per_turn_stt(self):
        from voice_runtime.providers import elevenlabs_stt

        assert not hasattr(elevenlabs_stt, "PerTurnStt"), (
            "PerTurnStt must be deleted"
        )
