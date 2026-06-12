"""NC-340: STT feed loop must survive a reconnect.

The HP-31 persona-caller bug: a long agent TTS turn (>10s) triggers
``set_speaking(False)`` → ``_connect()`` reconnect. The single ``_feed_audio``
task keeps ``send()``-ing into the socket being torn down; after 3 consecutive
failures it ``break``s permanently, and no path recreates it → permanent STT
deafness for the rest of the call.

These tests reproduce the *race* (post-reconnect frames must reach the new
socket), not merely the repair. ``test_long_tts_reconnect_resumes_feeding`` is
the AC-5 witness and fails on ``main``.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from voice_runtime.providers.elevenlabs_stt import PersistentSttSession

FRAME = b"\x10" * 160


class FakeScribe:
    """In-process stand-in for an ElevenLabs Scribe realtime socket.

    ``send`` raises once the socket is ``close()``d (mimicking a torn-down
    WebSocket). When a ``gate`` is supplied, ``close()`` blocks on it so a test
    can hold the close→connect swap window open deterministically.
    """

    def __init__(self, idx: int, gate: asyncio.Event | None = None) -> None:
        self.idx = idx
        self._gate = gate
        self.sent: list[dict] = []
        self.closed = False

    async def send(self, payload: dict) -> None:
        if self.closed:
            raise RuntimeError(f"socket {self.idx} is closed")
        self.sent.append(payload)

    async def close(self) -> None:
        self.closed = True
        if self._gate is not None:
            await self._gate.wait()

    def on(self, *_args, **_kwargs) -> None:
        return None


def _patch_elevenlabs(monkeypatch, make_socket) -> None:
    """Patch ``elevenlabs.ElevenLabs`` so ``_connect`` runs offline.

    The real ``_connect`` body executes (including the NC-340 feeder-liveness
    guard); only the network connect is replaced by ``make_socket``.
    """
    import elevenlabs

    class _Realtime:
        async def connect(self, _options):
            return make_socket()

    class _SpeechToText:
        realtime = _Realtime()

    class _Client:
        def __init__(self, *_args, **_kwargs) -> None:
            self.speech_to_text = _SpeechToText()

    monkeypatch.setattr(elevenlabs, "ElevenLabs", _Client)


def _socket_factory(gate_first: asyncio.Event | None = None):
    """Build a ``make_socket`` callable and the list it appends to.

    The first socket gets ``gate_first`` so that when the reconnect closes it,
    the swap window can be held open; later sockets are ungated.
    """
    sockets: list[FakeScribe] = []

    def make_socket() -> FakeScribe:
        idx = len(sockets)
        socket = FakeScribe(idx, gate=gate_first if idx == 0 else None)
        sockets.append(socket)
        return socket

    return make_socket, sockets


@pytest.mark.req("NC-340")
@pytest.mark.asyncio
async def test_long_tts_reconnect_resumes_feeding(monkeypatch) -> None:
    """AC-5 witness: after a long-TTS reconnect, frames reach the NEW socket.

    Reproduces the race — send() fails during the close→connect swap window —
    and asserts feeding resumes on the reconnected socket. Fails on ``main``
    (feeder breaks during the swap and is never restarted).
    """
    gate = asyncio.Event()
    make_socket, sockets = _socket_factory(gate_first=gate)
    _patch_elevenlabs(monkeypatch, make_socket)

    session = PersistentSttSession(api_key="test")
    session._loop = asyncio.get_running_loop()
    inbound: asyncio.Queue[bytes | None] = asyncio.Queue()
    await session.start(inbound)
    assert len(sockets) == 1

    # Initial feeding works.
    inbound.put_nowait(FRAME)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert sockets[0].sent, "feeder should send to the initial socket"

    # Trigger the long-TTS reconnect (agent spoke >10s).
    session._speaking = True
    session._speaking_since = time.monotonic() - 11.0
    session.set_speaking(False)

    # _connect closes socket0 and blocks on the gate: the swap window is open.
    await asyncio.sleep(0.01)
    assert sockets[0].closed, "reconnect should close the old socket"

    # During the window the feeder sends to the closed socket → send failures.
    for _ in range(4):
        inbound.put_nowait(FRAME)
    await asyncio.sleep(0.4)

    # Complete the reconnect: socket1 is created and swapped in.
    gate.set()
    await asyncio.sleep(0.05)
    assert len(sockets) == 2, "reconnect should open a new socket"

    # The new socket must receive post-reconnect audio.
    sockets[1].sent.clear()
    inbound.put_nowait(FRAME)
    await asyncio.sleep(0.2)
    assert sockets[1].sent, (
        "after a long-TTS reconnect the feeder must resume feeding the new "
        "socket; on main it broke during the swap and was never restarted "
        "(permanent STT deafness)"
    )

    inbound.put_nowait(None)
    await session.stop()


@pytest.mark.req("NC-340")
@pytest.mark.asyncio
async def test_send_failures_during_reconnect_do_not_break_feeder(monkeypatch) -> None:
    """AC-2: send failures that occur during a deliberate reconnect are transient.

    More than ``_MAX_CONSECUTIVE_SEND_FAILURES`` failures happen inside the swap
    window, yet the feeder task must stay alive. On main it ``break``s.
    """
    gate = asyncio.Event()
    make_socket, sockets = _socket_factory(gate_first=gate)
    _patch_elevenlabs(monkeypatch, make_socket)

    session = PersistentSttSession(api_key="test")
    session._loop = asyncio.get_running_loop()
    inbound: asyncio.Queue[bytes | None] = asyncio.Queue()
    await session.start(inbound)

    session._speaking = True
    session._speaking_since = time.monotonic() - 11.0
    session.set_speaking(False)
    await asyncio.sleep(0.01)
    assert sockets[0].closed

    # Feed more frames than the failure threshold while the socket is closed.
    for _ in range(session._MAX_CONSECUTIVE_SEND_FAILURES + 3):
        inbound.put_nowait(FRAME)
    await asyncio.sleep(0.6)

    assert session._feed_task is not None
    assert not session._feed_task.done(), (
        "feeder must survive send failures caused by a deliberate reconnect"
    )

    gate.set()
    inbound.put_nowait(None)
    await session.stop()


@pytest.mark.req("NC-340")
@pytest.mark.asyncio
async def test_fatal_error_reconnect_recreates_feeder(monkeypatch) -> None:
    """AC-3: the fatal-error reconnect path also restores a dead feeder."""
    make_socket, sockets = _socket_factory()
    _patch_elevenlabs(monkeypatch, make_socket)

    session = PersistentSttSession(api_key="test")
    session._loop = asyncio.get_running_loop()
    inbound: asyncio.Queue[bytes | None] = asyncio.Queue()
    await session.start(inbound)

    # Simulate the feeder having died.
    session._feed_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await session._feed_task
    assert session._feed_task.done()

    # A successful fatal-error reconnect must bring the feeder back.
    session._reconnect_attempt = 0
    await session._reconnect_after_error()

    assert session._feed_task is not None
    assert not session._feed_task.done(), (
        "fatal-error reconnect must recreate the feeder, not leave it dead"
    )

    inbound.put_nowait(FRAME)
    await asyncio.sleep(0.05)
    assert sockets[-1].sent, "feeder should feed the reconnected socket"

    inbound.put_nowait(None)
    await session.stop()


@pytest.mark.req("NC-340")
@pytest.mark.asyncio
async def test_reconnect_exhaustion_still_fires_on_error(monkeypatch) -> None:
    """AC-4 (guard): exhausting reconnect attempts still surfaces via on_error.

    The bounded-retry + on_error-on-exhaustion contract must be preserved by the
    NC-340 change.
    """
    errors: list[str] = []
    session = PersistentSttSession(api_key="test")
    session._loop = asyncio.get_running_loop()
    session._inbound_queue = asyncio.Queue()
    session.on_error = errors.append
    session._reconnect_attempt = session._MAX_RECONNECT_ATTEMPTS

    await session._reconnect_after_error()

    assert errors, "on_error must fire when reconnect attempts are exhausted"
    assert "reconnect_exhausted" in errors[0]
