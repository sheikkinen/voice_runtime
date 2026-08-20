"""VR-005 RED witnesses: STT session must own every task it spawns.

Three defects in one family (all field-witnessed 2026-08-20):
  D-A: initial ``start()`` double-creates the feed task — ``_connect()``'s
       ``_ensure_feed_task()`` creates task A, then ``start()`` overwrites
       the only reference with task B. Task A is orphaned forever.
  D-B: reconnect work scheduled via ``run_coroutine_threadsafe`` (long-TTS
       ``_connect()``, fatal-error ``_reconnect_after_error()``) drops the
       returned future — ``stop()`` cannot reach it.
  D-C: ``stop()`` cancels without awaiting — "stopped" is intent, not state.

The ``_patch_elevenlabs`` harness runs the REAL ``_connect`` offline (the
NC-340 pattern) — a no-op ``_connect`` stub would miss D-A entirely, which
is exactly how the csap-black FR-005 reproducer missed it.

Azure seam (AC-05): no double-create there, but ``_on_canceled()`` drops
its reconnect future the same way.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest
from voice_runtime.providers.elevenlabs_stt import PersistentSttSession

from tests.conftest import requires_azure

FRAME = b"\x10" * 160


class FakeScribe:
    """Instant-close Scribe stand-in: ``close()`` never suspends, so a
    cancellation that is merely *scheduled* (not awaited) stays undelivered
    — making D-C observable."""

    def __init__(self, idx: int) -> None:
        self.idx = idx
        self.sent: list[dict] = []
        self.closed = False

    async def send(self, payload: dict) -> None:
        if self.closed:
            raise RuntimeError(f"socket {self.idx} is closed")
        self.sent.append(payload)

    async def close(self) -> None:
        self.closed = True

    def on(self, *_args, **_kwargs) -> None:
        return None


def _patch_elevenlabs(monkeypatch, connect_gate: asyncio.Event | None = None):
    """Run the real ``_connect`` offline; optional gate blocks reconnects.

    Only the network ``connect`` is replaced — ``_ensure_feed_task()`` and
    the rest of the real ``_connect`` body execute, faithful to the
    task-spawning behavior (R-3).
    """
    import elevenlabs

    sockets: list[FakeScribe] = []

    class _Realtime:
        async def connect(self, _options):
            if connect_gate is not None and sockets:
                await connect_gate.wait()
            socket = FakeScribe(len(sockets))
            sockets.append(socket)
            return socket

    class _SpeechToText:
        realtime = _Realtime()

    class _Client:
        def __init__(self, *_args, **_kwargs) -> None:
            self.speech_to_text = _SpeechToText()

    monkeypatch.setattr(elevenlabs, "ElevenLabs", _Client)
    return sockets


def _live_tasks(fragment: str) -> list[asyncio.Task]:
    """Pending tasks whose name or coroutine qualname contains fragment."""
    found = []
    for task in asyncio.all_tasks():
        if task.done():
            continue
        qualname = getattr(task.get_coro(), "__qualname__", "")
        if fragment in task.get_name() or fragment in qualname:
            found.append(task)
    return found


async def _drain(tasks: list[asyncio.Task]) -> None:
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.req("VR-005")
@pytest.mark.asyncio
async def test_initial_start_creates_exactly_one_feed_task(monkeypatch) -> None:
    """AC-01 / D-A: ``start()`` must create exactly one session-owned feeder.

    Fails on 0.1.12: ``_connect()`` creates task A via ``_ensure_feed_task``,
    then ``start()`` creates task B — two feeders race one inbound queue.
    """
    _patch_elevenlabs(monkeypatch)
    session = PersistentSttSession(api_key="test")
    await session.start(asyncio.Queue())
    try:
        feeders = _live_tasks("stt_feed")
        assert len(feeders) == 1, (
            f"expected exactly one feed task, found {len(feeders)} "
            "(the orphaned duplicate is the defect)"
        )
    finally:
        await _drain(_live_tasks("stt_feed"))


@pytest.mark.req("VR-005")
@pytest.mark.asyncio
async def test_stop_awaits_feed_task_and_closes_connection(monkeypatch) -> None:
    """AC-02 / AC-06 / D-C: after ``stop()`` returns, all session-owned
    tasks are done and the SDK connection is closed.

    Fails on 0.1.12: ``stop()`` schedules cancellation but never awaits it,
    and the D-A orphan survives ``stop()`` entirely.
    """
    sockets = _patch_elevenlabs(monkeypatch)
    session = PersistentSttSession(api_key="test")
    await session.start(asyncio.Queue())
    await session.stop()
    try:
        assert session._feed_task is not None and session._feed_task.done(), (
            "stop() must await the cancelled feed task, not merely cancel()"
        )
        assert sockets[-1].closed, "stop() must close the SDK connection"
        leftovers = _live_tasks("stt_feed")
        assert not leftovers, (
            f"{len(leftovers)} feed task(s) still pending after stop()"
        )
    finally:
        await _drain(_live_tasks("stt_feed"))


@pytest.mark.req("VR-005")
@pytest.mark.asyncio
async def test_long_tts_reconnect_future_owned(monkeypatch) -> None:
    """AC-03 / D-B: the long-TTS reconnect future is owned — ``stop()``
    cancels and drains it.

    Fails on 0.1.12: ``set_speaking(False)`` drops the future returned by
    ``run_coroutine_threadsafe(self._connect(), loop)``.
    """
    gate = asyncio.Event()
    _patch_elevenlabs(monkeypatch, connect_gate=gate)
    session = PersistentSttSession(api_key="test")
    await session.start(asyncio.Queue())

    session._speaking = True
    session._speaking_since = time.monotonic() - 11.0
    session.set_speaking(False)  # schedules _connect() on the loop
    await asyncio.sleep(0.05)
    assert _live_tasks("._connect"), "precondition: reconnect in flight"

    await session.stop()
    await asyncio.sleep(0)
    try:
        pending = _live_tasks("._connect")
        assert not pending, "stop() must cancel and drain the owned reconnect"
    finally:
        gate.set()
        await _drain(_live_tasks("._connect") + _live_tasks("stt_feed"))


@pytest.mark.req("VR-005")
@pytest.mark.asyncio
async def test_fatal_error_reconnect_future_owned(monkeypatch) -> None:
    """AC-03 / D-B: the fatal-error reconnect future is owned too.

    Fails on 0.1.12: ``_on_error`` drops the future for
    ``_reconnect_after_error()`` (parked in backoff sleep at stop time).
    """
    _patch_elevenlabs(monkeypatch)
    session = PersistentSttSession(api_key="test")
    await session.start(asyncio.Queue())

    session._on_error({"message_type": "server_error"})
    await asyncio.sleep(0.05)
    assert _live_tasks("_reconnect_after_error"), "precondition: reconnect scheduled"

    await session.stop()
    await asyncio.sleep(0)
    try:
        pending = _live_tasks("_reconnect_after_error")
        assert not pending, "stop() must cancel and drain the owned reconnect"
    finally:
        await _drain(_live_tasks("_reconnect_after_error") + _live_tasks("stt_feed"))


@requires_azure
@pytest.mark.req("VR-005")
@pytest.mark.asyncio
async def test_azure_canceled_reconnect_future_owned() -> None:
    """AC-05: Azure's ``_on_canceled`` reconnect future is owned —
    ``stop()`` cancels and drains it.

    Fails on 0.1.12: the ``run_coroutine_threadsafe`` result is dropped
    (azure_stt.py _on_canceled), so the reconnect survives ``stop()``
    parked in backoff sleep.
    """
    import azure.cognitiveservices.speech as speechsdk
    from voice_runtime.providers.azure_stt import AzurePersistentStt

    with (
        patch("azure.cognitiveservices.speech.SpeechConfig"),
        patch("azure.cognitiveservices.speech.audio.AudioStreamFormat"),
        patch("azure.cognitiveservices.speech.audio.PushAudioInputStream"),
        patch("azure.cognitiveservices.speech.audio.AudioConfig"),
        patch("azure.cognitiveservices.speech.SpeechRecognizer"),
    ):
        stt = AzurePersistentStt(subscription_key="test-key")
        await stt.start(asyncio.Queue())

        evt = MagicMock()
        evt.cancellation_details.reason = speechsdk.CancellationReason.Error
        evt.cancellation_details.error_details = "boom"
        stt._on_canceled(evt)
        await asyncio.sleep(0.05)
        assert _live_tasks("_reconnect_after_error"), (
            "precondition: reconnect scheduled"
        )

        await stt.stop()
        await asyncio.sleep(0)
        try:
            pending = _live_tasks("_reconnect_after_error")
            assert not pending, "stop() must cancel and drain the owned reconnect"
        finally:
            await _drain(_live_tasks("_reconnect_after_error"))
