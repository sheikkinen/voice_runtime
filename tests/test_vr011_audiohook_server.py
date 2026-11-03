"""VR-011: AudioHook server transport (Genesys Audio Connector, minimal PoC).

Drives the captured route handler with a fake socket that scripts Genesys
messages and records every send with a fake-clock timestamp and the task
that performed it. The module's ``_now``/``_sleep`` are patched with a
virtual clock, so no test waits in real time for protocol timeouts.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import logging
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

MODULE = "voice_runtime.transports.audiohook_server"
MODULE_PRESENT = importlib.util.find_spec(MODULE) is not None
needs_module = pytest.mark.skipif(
    not MODULE_PRESENT, reason="VR-011 RED: audiohook_server not implemented yet"
)

pytestmark = pytest.mark.req("VR-011")

API_KEY = "vr011-test-key-Zq7"
ORG_ID = "org-1111"
SESSION_ID = "e160e428-53e2-487c-977d-96989bf5c99d"
CONVERSATION_ID = "conv-2222"
ANI = "tel:+358401234567"
PCMU = {"type": "audio", "format": "PCMU", "channels": ["external"], "rate": 8000}
STEREO = {
    "type": "audio",
    "format": "PCMU",
    "channels": ["external", "internal"],
    "rate": 8000,
}
LATENCY = 0.2


# ---------------------------------------------------------------- harness


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def now(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.t += max(0.0, seconds)
        await asyncio.sleep(0)


@dataclass
class Sent:
    t: float
    kind: str  # "text" | "bytes"
    payload: Any
    task: asyncio.Task | None


class FakeAudiohookSocket:
    def __init__(self, clock: FakeClock, headers: dict[str, str]) -> None:
        self.clock = clock
        self.headers = headers
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.sent: list[Sent] = []
        self.accepted_at: float | None = None
        self.closed_at: float | None = None
        self.fail_bytes = False
        self.on_text = None  # callback(msg_dict) for reactive scripting
        self.order: list[str] | None = None
        self._gseq = 0

    async def accept(self) -> None:
        self.accepted_at = self.clock.now()

    async def receive(self) -> dict:
        item = await self.incoming.get()
        if isinstance(item, BaseException):
            raise item
        return item

    async def send_text(self, data: str) -> None:
        msg = json.loads(data)
        self.sent.append(Sent(self.clock.now(), "text", msg, asyncio.current_task()))
        if self.order is not None:
            self.order.append(f"send:{msg['type']}")
        if self.on_text is not None:
            self.on_text(msg)

    async def send_bytes(self, data: bytes) -> None:
        if self.fail_bytes:
            raise RuntimeError("socket write failed")
        self.sent.append(
            Sent(self.clock.now(), "bytes", bytes(data), asyncio.current_task())
        )

    async def close(self, code: int = 1000) -> None:
        if self.closed_at is None:
            self.closed_at = self.clock.now()

    # --- Genesys side
    def push(self, type_: str, parameters: dict | None = None, *, seq=None) -> None:
        if seq is None:
            self._gseq += 1
            seq = self._gseq
        else:
            self._gseq = seq
        msg = {
            "version": "2",
            "id": SESSION_ID,
            "type": type_,
            "seq": seq,
            "serverseq": 0,
            "position": "PT0S",
            "parameters": parameters if parameters is not None else {},
        }
        self.incoming.put_nowait({"type": "websocket.receive", "text": json.dumps(msg)})

    def push_open(
        self,
        *,
        conversation_id=CONVERSATION_ID,
        media=None,
        org=ORG_ID,
        participant_id="p1",
    ):
        self.push(
            "open",
            {
                "organizationId": org,
                "conversationId": conversation_id,
                "participant": {
                    "id": participant_id,
                    "ani": ANI,
                    "aniName": "",
                    "dnis": "+3589",
                },
                "media": media if media is not None else [STEREO, PCMU],
                "language": "fi-FI",
                "inputVariables": {},
            },
        )

    def push_bytes(self, data: bytes) -> None:
        self.incoming.put_nowait({"type": "websocket.receive", "bytes": data})

    def push_disconnect(self) -> None:
        self.incoming.put_nowait({"type": "websocket.disconnect", "code": 1006})

    # --- inspection
    def texts(self) -> list[dict]:
        return [s.payload for s in self.sent if s.kind == "text"]

    def text_types(self) -> list[str]:
        return [m["type"] for m in self.texts()]

    def binaries(self) -> list[Sent]:
        return [s for s in self.sent if s.kind == "bytes"]


def headers(*, api_key=API_KEY, org=ORG_ID) -> dict[str, str]:
    h = {
        "audiohook-session-id": SESSION_ID,
        "audiohook-correlation-id": "corr-1",
    }
    if api_key is not None:
        h["x-api-key"] = api_key
    if org is not None:
        h["audiohook-organization-id"] = org
    return h


class FakeStt:
    def __init__(self, order: list[str] | None = None) -> None:
        self.order = order
        self.start_calls = 0
        self.stop_calls = 0
        self.cancelled = False

    async def start(self, audio_queue) -> None:
        self.start_calls += 1
        if self.order is not None:
            self.order.append("stt.start")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def stop(self) -> None:
        self.stop_calls += 1


@dataclass
class Rig:
    mod: Any
    clock: FakeClock
    session: Any
    ws: FakeAudiohookSocket
    task: asyncio.Task
    stt: FakeStt
    disconnects: list
    factory_calls: list


def capture_handler(mod, session, **kwargs):
    captured = {}

    def fake_websocket(path):
        def decorator(fn):
            captured["path"] = path
            captured["fn"] = fn
            return fn

        return decorator

    app = MagicMock()
    app.websocket = fake_websocket
    kwargs.setdefault("api_key", API_KEY)
    mod.register_audiohook_websocket(app, session, **kwargs)
    return captured["fn"]


async def start_rig(
    monkeypatch,
    *,
    hdrs: dict | None = None,
    order: list | None = None,
    secondary: bool = False,
    **register_kwargs,
) -> Rig:
    import voice_runtime.transports.audiohook_server as mod
    from voice_runtime.session import VoiceSession

    clock = FakeClock()
    monkeypatch.setattr(mod, "_now", clock.now)
    monkeypatch.setattr(mod, "_sleep", clock.sleep)

    session = VoiceSession()
    session.set_loop(asyncio.get_running_loop())
    stt = FakeStt(order)
    factory_calls: list = []

    def factory():
        factory_calls.append(clock.now())
        if order is not None:
            order.append("stt.factory")
        return stt

    session.stt_factory = factory
    if secondary:
        session.stt_secondary_factory = lambda: FakeStt()
    disconnects: list = []
    original = session.signal_disconnected

    def spy_disconnected():
        disconnects.append(clock.now())
        original()

    session.signal_disconnected = spy_disconnected

    register_kwargs.setdefault("organization_id", ORG_ID)
    register_kwargs.setdefault("mark_latency_s", LATENCY)
    handler = capture_handler(mod, session, **register_kwargs)
    ws = FakeAudiohookSocket(clock, hdrs if hdrs is not None else headers())
    ws.order = order
    task = asyncio.create_task(handler(ws))
    return Rig(mod, clock, session, ws, task, stt, disconnects, factory_calls)


async def until(predicate, spins: int = 50_000) -> bool:
    for _ in range(spins):
        if predicate():
            return True
        await asyncio.sleep(0)
    return predicate()


async def finish(rig: Rig) -> None:
    """Peer close; wait for the handler to return."""
    if not rig.task.done():
        rig.ws.push("close", {"reason": "end"})
    assert await until(rig.task.done), "handler did not finish"
    with contextlib.suppress(asyncio.CancelledError):
        await rig.task


async def open_call(rig: Rig) -> None:
    rig.ws.push_open()
    assert await until(lambda: rig.session.stream_sid is not None)
    assert await until(lambda: rig.stt.start_calls == 1)


def mark(session, name: str) -> threading.Event:
    """Session contract of send_mark_and_wait without the blocking wait."""
    ev = threading.Event()
    session._pending_marks[name] = ev
    session._mark_queue.put_nowait(name)
    return ev


def queue_audio(session, data: bytes, chunk: int = 160) -> None:
    for i in range(0, len(data), chunk):
        session.outbound.put_nowait(data[i : i + chunk])


# ---------------------------------------------------------------- AC-01


def test_ac01_audiohook_server_module_exists():
    """AC-01: RED witness — the transport module must exist."""
    assert MODULE_PRESENT, f"{MODULE} is missing"


# ---------------------------------------------------------------- AC-02


@needs_module
@pytest.mark.parametrize(
    "kwargs",
    [
        {"api_key": ""},
        {"api_key": None},
        {"api_key": 1234},
        {"api_key": API_KEY, "mark_latency_s": -0.1},
        {"api_key": API_KEY, "mark_latency_s": float("nan")},
        {"api_key": API_KEY, "mark_latency_s": float("inf")},
    ],
)
def test_ac02_registration_validation(kwargs):
    import voice_runtime.transports.audiohook_server as mod
    from voice_runtime.session import VoiceSession

    app = MagicMock()
    with pytest.raises(ValueError) as exc:
        mod.register_audiohook_websocket(app, VoiceSession(), **kwargs)
    app.websocket.assert_not_called()
    assert API_KEY not in str(exc.value)
    assert API_KEY not in repr(exc.value)


@needs_module
@pytest.mark.asyncio
async def test_ac02_secrets_absent_from_logs(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    wrong = "presented-wrong-key-Xy9"
    rig = await start_rig(monkeypatch, hdrs=headers(api_key=wrong))
    rig.ws.push_open()
    assert await until(rig.task.done)
    ok = await start_rig(monkeypatch)
    await open_call(ok)
    await finish(ok)
    text = caplog.text
    assert API_KEY not in text
    assert wrong not in text


# ---------------------------------------------------------------- AC-03


@needs_module
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("hdrs", "open_org"),
    [
        (headers(api_key="wrong"), ORG_ID),
        (headers(api_key=None), ORG_ID),
        (headers(org="other-org"), ORG_ID),
        (headers(), "other-org"),
    ],
    ids=["wrong-key", "missing-key", "header-org", "open-org"],
)
async def test_ac03_unauthorized(monkeypatch, hdrs, open_org):
    rig = await start_rig(monkeypatch, hdrs=hdrs)
    rig.ws.push_open(org=open_org)
    assert await until(rig.task.done)
    texts = rig.ws.texts()
    assert [m["type"] for m in texts] == ["disconnect"]
    assert texts[0]["parameters"]["reason"] == "unauthorized"
    assert texts[0]["parameters"].get("info", "") == "unauthorized"
    assert texts[0]["clientseq"] == 1
    assert rig.ws.sent[0].t - rig.ws.accepted_at >= 0.5
    assert rig.ws.closed_at is not None and rig.ws.closed_at >= rig.ws.sent[0].t
    assert rig.session.call_sid is None
    assert rig.session.stream_sid is None
    assert rig.factory_calls == []
    assert rig.disconnects == []


# ---------------------------------------------------------------- AC-04


@needs_module
@pytest.mark.asyncio
async def test_ac04_probe(monkeypatch):
    rig = await start_rig(monkeypatch)
    rig.ws.push_open(conversation_id=None, media=[PCMU])
    assert await until(lambda: "opened" in rig.ws.text_types())
    opened = rig.ws.texts()[0]
    assert opened["type"] == "opened"
    assert opened["parameters"]["media"] == [PCMU]
    assert rig.ws.sent[0].t - rig.ws.accepted_at < 5.0
    rig.ws.push("close", {"reason": "end"})
    assert await until(rig.task.done)
    assert rig.ws.text_types() == ["opened", "closed"]
    assert rig.session.stream_sid is None
    assert not rig.session._ws_connected.is_set()
    assert rig.factory_calls == []
    assert rig.disconnects == []


NIL_UUID = "00000000-0000-0000-0000-000000000000"


@needs_module
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "open_kwargs",
    [
        {"conversation_id": NIL_UUID},
        {"participant_id": NIL_UUID},
    ],
    ids=["nil-conversation", "nil-participant"],
)
async def test_ac04_probe_nil_uuid(monkeypatch, open_kwargs):
    """E-1 finding: Genesys probes carry the nil UUID, not JSON null.

    The reference client sends ``conversationId`` and ``participant.id`` as
    ``00000000-0000-0000-0000-000000000000``; its servers treat either as a
    probe (``isNullUuid``).
    """
    rig = await start_rig(monkeypatch)
    rig.ws.push_open(media=[PCMU], **open_kwargs)
    assert await until(lambda: "opened" in rig.ws.text_types())
    rig.ws.push("close", {"reason": "end"})
    assert await until(rig.task.done)
    assert rig.ws.text_types() == ["opened", "closed"]
    assert rig.session.stream_sid is None
    assert rig.session.call_sid is None
    assert not rig.session._ws_connected.is_set()
    assert rig.factory_calls == []
    assert rig.disconnects == []


@needs_module
def test_ac04_probe_through_real_fastapi_route():
    """E-1 finding: the fake socket bypasses FastAPI's endpoint injection.

    Drive the registered route through a real Starlette upgrade so an
    unresolvable ``websocket`` annotation (403 before the handler) fails here.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from voice_runtime.session import VoiceSession
    from voice_runtime.transports.audiohook_server import (
        register_audiohook_websocket,
    )

    app = FastAPI()
    session = VoiceSession()
    register_audiohook_websocket(app, session, api_key=API_KEY, organization_id=ORG_ID)
    open_msg = {
        "version": "2",
        "id": SESSION_ID,
        "type": "open",
        "seq": 1,
        "serverseq": 0,
        "position": "PT0S",
        "parameters": {
            "organizationId": ORG_ID,
            "conversationId": None,
            "participant": None,
            "media": [PCMU],
            "language": "en-US",
        },
    }
    close_msg = {**open_msg, "type": "close", "seq": 2, "parameters": {}}
    with (
        TestClient(app) as client,
        client.websocket_connect("/audiohook", headers=headers()) as ws,
    ):
        ws.send_text(json.dumps(open_msg))
        opened = json.loads(ws.receive_text())
        ws.send_text(json.dumps(close_msg))
        closed = json.loads(ws.receive_text())
    assert opened["type"] == "opened"
    assert opened["parameters"]["media"] == [PCMU]
    assert closed["type"] == "closed"
    assert [opened["seq"], closed["seq"]] == [1, 2]
    assert session.stream_sid is None


# ---------------------------------------------------------------- AC-05


@needs_module
@pytest.mark.asyncio
async def test_ac05_call_open_binds_after_opened(monkeypatch):
    order: list[str] = []
    rig = await start_rig(monkeypatch, order=order)
    identity_at_opened: dict = {}
    ready: list = []
    rig.session.on_stt_ready = lambda stt: (ready.append(stt), order.append("ready"))

    def on_text(msg):
        if msg["type"] == "opened":
            identity_at_opened["call_sid"] = rig.session.call_sid
            identity_at_opened["stream_sid"] = rig.session.stream_sid

    rig.ws.on_text = on_text
    await open_call(rig)
    assert rig.ws.texts()[0]["type"] == "opened"
    assert rig.ws.texts()[0]["parameters"]["media"] == [PCMU]
    assert rig.ws.sent[0].t - rig.ws.accepted_at < 5.0
    assert identity_at_opened == {"call_sid": None, "stream_sid": None}
    assert order[:4] == ["send:opened", "stt.factory", "ready", "stt.start"]
    assert rig.session.call_sid == CONVERSATION_ID
    assert rig.session.caller_number == ANI
    assert rig.session.stream_sid == SESSION_ID
    assert ready == [rig.stt]
    await finish(rig)


@needs_module
@pytest.mark.asyncio
async def test_ac05_secondary_factory_tee(monkeypatch):
    from voice_runtime.stt_tee import SttTee

    rig = await start_rig(monkeypatch, secondary=True)
    rig.ws.push_open()
    assert await until(lambda: isinstance(rig.session.stt, SttTee))
    await finish(rig)


@needs_module
@pytest.mark.asyncio
async def test_ac05_unsupported_media(monkeypatch):
    rig = await start_rig(monkeypatch)
    rig.ws.push_open(media=[STEREO, {**PCMU, "format": "L16"}])
    assert await until(rig.task.done)
    texts = rig.ws.texts()
    assert [m["type"] for m in texts] == ["disconnect"]
    assert texts[0]["parameters"]["reason"] == "error"
    assert rig.session.stream_sid is None
    assert rig.factory_calls == []
    assert rig.disconnects == []
    assert rig.ws.closed_at is not None


# ---------------------------------------------------------------- AC-06


@needs_module
@pytest.mark.asyncio
async def test_ac06_envelope_and_sequencing(monkeypatch):
    rig = await start_rig(monkeypatch)
    await open_call(rig)
    ping_at = rig.clock.now()
    rig.ws.push("ping")
    assert await until(lambda: "pong" in rig.ws.text_types())
    pong = next(
        s for s in rig.ws.sent if s.kind == "text" and s.payload["type"] == "pong"
    )
    assert pong.t - ping_at <= 0.2
    update_at = rig.clock.now()
    rig.ws.push("update", {"inputVariables": {}})
    assert await until(lambda: "updated" in rig.ws.text_types())
    updated = next(
        s for s in rig.ws.sent if s.kind == "text" and s.payload["type"] == "updated"
    )
    assert updated.t - update_at <= 0.2
    rig.ws.push_bytes(b"\x7f" * 320)
    await finish(rig)

    texts = rig.ws.texts()
    assert [m["type"] for m in texts] == ["opened", "pong", "updated", "closed"]
    assert [m["seq"] for m in texts] == [1, 2, 3, 4]
    assert [m["clientseq"] for m in texts] == [1, 2, 3, 4]
    for m in texts:
        assert m["version"] == "2"
        assert m["id"] == SESSION_ID
        assert isinstance(m["parameters"], dict)
        assert "position" not in m


# ---------------------------------------------------------------- AC-07


@needs_module
@pytest.mark.asyncio
@pytest.mark.parametrize("bad_seq", [3, 1], ids=["skipped", "repeated"])
async def test_ac07_sequence_fault(monkeypatch, caplog, bad_seq):
    caplog.set_level(logging.ERROR)
    rig = await start_rig(monkeypatch)
    await open_call(rig)
    rig.ws.push("ping", seq=bad_seq)
    assert await until(rig.task.done)
    errors = [
        r
        for r in caplog.records
        if r.levelno == logging.ERROR and "sequence" in r.getMessage().lower()
    ]
    assert len(errors) == 1
    last = rig.ws.texts()[-1]
    assert last["type"] == "disconnect"
    assert last["parameters"]["reason"] == "error"
    assert len(rig.disconnects) == 1
    assert rig.stt.stop_calls == 1


# ---------------------------------------------------------------- AC-08


@needs_module
@pytest.mark.asyncio
async def test_ac08_inbound_rechunking(monkeypatch):
    rig = await start_rig(monkeypatch)
    frames: list = []
    taps: list = []
    rig.session.put_inbound = lambda data: frames.append(data)
    rig.session.tap_caller = lambda data: taps.append(data)
    await open_call(rig)
    sizes = [1, 159, 160, 333, 4000]
    payload = bytes(i % 251 for i in range(sum(sizes)))
    offset = 0
    for size in sizes:
        rig.ws.push_bytes(payload[offset : offset + size])
        offset += size
    full = len(payload) // 160
    assert await until(lambda: len([f for f in frames if f]) == full)
    await finish(rig)
    audio = [f for f in frames if f is not None]
    assert len(audio) == full
    assert all(len(f) == 160 for f in audio)
    assert b"".join(audio) == payload[: full * 160]
    assert taps == audio


# ---------------------------------------------------------------- AC-09


@needs_module
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("total", "packets", "pad"), [(80_000, 50, 0), (80_160, 51, 1440)]
)
async def test_ac09_outbound_pacing(monkeypatch, total, packets, pad):
    rig = await start_rig(monkeypatch)
    assert rig.mod.PACKET_INTERVAL_S == 0.2
    assert rig.mod.PACKET_BYTES == 1600
    await open_call(rig)
    data = bytes(i % 255 for i in range(total))  # never 0xFF
    queue_audio(rig.session, data)
    assert await until(lambda: len(rig.ws.binaries()) >= packets)
    settle = rig.clock.now() + 2.0
    assert await until(lambda: rig.clock.now() >= settle)
    bins = rig.ws.binaries()
    assert len(bins) == packets
    assert all(len(b.payload) == 1600 for b in bins)
    joined = b"".join(b.payload for b in bins)
    assert joined[:total] == data
    assert joined[total:] == b"\xff" * pad
    gaps = [b.t - a.t for a, b in zip(bins, bins[1:], strict=False)]
    assert min(gaps) >= 0.2 - 1e-9
    await finish(rig)


# ---------------------------------------------------------------- AC-10


async def _writer_tasks(rig: Rig) -> set:
    tasks = {s.task for s in rig.ws.sent}
    assert rig.task not in tasks
    return tasks


@needs_module
@pytest.mark.asyncio
async def test_ac10_single_writer_all_paths(monkeypatch):
    # authentication failure
    rig = await start_rig(monkeypatch, hdrs=headers(api_key="wrong"))
    rig.ws.push_open()
    assert await until(rig.task.done)
    assert len(await _writer_tasks(rig)) == 1
    # probe
    rig = await start_rig(monkeypatch)
    rig.ws.push_open(conversation_id=None)
    await finish(rig)
    assert len(await _writer_tasks(rig)) == 1
    # unsupported media
    rig = await start_rig(monkeypatch)
    rig.ws.push_open(media=[STEREO])
    assert await until(rig.task.done)
    assert len(await _writer_tasks(rig)) == 1
    # control + audio + clear + bot ending
    rig = await start_rig(monkeypatch)
    await open_call(rig)
    queue_audio(rig.session, b"\x01" * 16_000)
    assert await until(lambda: len(rig.ws.binaries()) >= 2)
    ping_at = rig.clock.now()
    rig.ws.push("ping")
    assert await until(lambda: "pong" in rig.ws.text_types())
    pong = next(
        s for s in rig.ws.sent if s.kind == "text" and s.payload["type"] == "pong"
    )
    assert pong.t - ping_at <= rig.mod.PACKET_INTERVAL_S
    rig.session.request_clear_buffer()
    assert await until(lambda: "event" in rig.ws.text_types())
    rig.ws.on_text = lambda m: (
        rig.ws.push("close", {"reason": "disconnect"})
        if m["type"] == "disconnect"
        else None
    )
    rig.session.request_disconnect()
    assert await until(rig.task.done)
    assert "closed" in rig.ws.text_types()
    assert len(await _writer_tasks(rig)) == 1


# ---------------------------------------------------------------- AC-11


class RecordingQueue(asyncio.Queue):
    def __init__(self, clock: FakeClock) -> None:
        super().__init__()
        self.clock = clock
        self.taken: list[tuple[str, float]] = []

    def get_nowait(self):
        item = super().get_nowait()
        self.taken.append((item, self.clock.now()))
        return item


def _resolution_spy(rig: Rig) -> list[tuple[str, float]]:
    resolved: list[tuple[str, float]] = []
    original = rig.session.signal_mark_received

    def spy(name):
        resolved.append((name, rig.clock.now()))
        original(name)

    rig.session.signal_mark_received = spy
    return resolved


def _packet_time_containing(bins: list[Sent], byte_index: int) -> float:
    total = 0
    for b in bins:
        total += len(b.payload)
        if total >= byte_index:
            return b.t
    raise AssertionError(f"byte {byte_index} never sent")


@needs_module
@pytest.mark.asyncio
async def test_ac11_byte_clock_marks(monkeypatch):
    rig = await start_rig(monkeypatch)
    resolved = _resolution_spy(rig)
    await open_call(rig)
    # A TTS thread queues a clip, then its mark; the next clip follows only
    # after the writer has consumed the mark (send_mark_and_wait blocks).
    queue_audio(rig.session, b"\x01" * 1600)
    ev_a = mark(rig.session, "a")
    assert await until(rig.session._mark_queue.empty)
    queue_audio(rig.session, b"\x02" * 1600)
    ev_b = mark(rig.session, "b")
    assert await until(rig.session._mark_queue.empty)
    queue_audio(rig.session, b"\x03" * 16_000)
    assert await until(lambda: ev_a.is_set() and ev_b.is_set())
    assert await until(lambda: len(rig.ws.binaries()) >= 8)
    bins = rig.ws.binaries()
    assert [n for n, _ in resolved[:2]] == ["a", "b"]
    t_a = dict(resolved)["a"]
    t_b = dict(resolved)["b"]
    assert t_a >= _packet_time_containing(bins, 1600) + LATENCY
    assert t_b >= _packet_time_containing(bins, 3200) + LATENCY
    # audio queued after each snapshot does not delay that mark
    assert t_a < _packet_time_containing(bins, 4800)
    assert t_b < _packet_time_containing(bins, 3200 + 8000)
    await finish(rig)


@needs_module
@pytest.mark.asyncio
async def test_ac11_mark_without_audio(monkeypatch):
    rig = await start_rig(monkeypatch)
    rq = RecordingQueue(rig.clock)
    rig.session._mark_queue = rq
    resolved = _resolution_spy(rig)
    await open_call(rig)
    ev = mark(rig.session, "solo")
    assert await until(ev.is_set)
    (name, taken_at) = rq.taken[0]
    assert name == "solo"
    assert resolved[0][1] >= taken_at + LATENCY
    assert rig.ws.binaries() == []
    await finish(rig)


# ---------------------------------------------------------------- AC-12


@needs_module
@pytest.mark.asyncio
async def test_ac12_playback_evidence_logs_and_resolves_nothing(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    rig = await start_rig(monkeypatch, mark_latency_s=1000.0)
    await open_call(rig)
    queue_audio(rig.session, b"\x01" * 1600)
    ev = mark(rig.session, "held")
    assert await until(lambda: len(rig.ws.binaries()) == 1)
    rig.ws.push("playback_started", {})
    rig.ws.push("playback_completed", {})
    assert await until(
        lambda: sum("playback_completed" in r.getMessage() for r in caplog.records) == 1
    )
    for kind in ("playback_started", "playback_completed"):
        recs = [
            r
            for r in caplog.records
            if r.levelno == logging.INFO and kind in r.getMessage()
        ]
        assert len(recs) == 1, kind
        msg = recs[0].getMessage()
        assert "PT0S" in msg
        assert "byte_clock=0.200s" in msg
        assert "last_mark=" in msg
        assert "since_mark=" in msg
        assert API_KEY not in msg
    assert not ev.is_set()
    await finish(rig)


# ---------------------------------------------------------------- AC-13


@needs_module
@pytest.mark.asyncio
async def test_ac13_barge_in(monkeypatch):
    rig = await start_rig(monkeypatch)
    await open_call(rig)
    queue_audio(rig.session, b"\x11" * 16_000)
    pending = mark(rig.session, "tts_complete")
    assert await until(lambda: len(rig.ws.binaries()) >= 2)
    clear_at_index = len(rig.ws.sent)
    rig.session.request_clear_buffer()
    assert await until(lambda: "event" in rig.ws.text_types())
    assert await until(pending.is_set)
    event_index = next(
        i
        for i, s in enumerate(rig.ws.sent)
        if s.kind == "text" and s.payload["type"] == "event"
    )
    events = [m for m in rig.ws.texts() if m["type"] == "event"]
    assert len(events) == 1
    assert events[0]["parameters"]["entities"] == [{"type": "barge_in", "data": {}}]
    between = [s for s in rig.ws.sent[clear_at_index:event_index] if s.kind == "bytes"]
    assert len(between) <= 1
    queue_audio(rig.session, b"\x22" * 3200)
    settle = rig.clock.now() + 1.0
    assert await until(lambda: rig.clock.now() >= settle)
    after = [s.payload for s in rig.ws.sent[event_index + 1 :] if s.kind == "bytes"]
    assert after, "post-clear audio should still play"
    assert all(b"\x11" not in p for p in after)
    await finish(rig)


# ---------------------------------------------------------------- AC-14


@needs_module
@pytest.mark.asyncio
async def test_ac14_bot_disconnect_with_close(monkeypatch):
    rig = await start_rig(monkeypatch)
    await open_call(rig)
    rig.ws.on_text = lambda m: (
        rig.ws.push("close", {"reason": "disconnect"})
        if m["type"] == "disconnect"
        else None
    )
    rig.session.request_disconnect()
    assert await until(rig.task.done)
    texts = rig.ws.texts()
    assert [m["type"] for m in texts][-2:] == ["disconnect", "closed"]
    assert texts[-2]["parameters"] == {"reason": "completed", "outputVariables": {}}
    closed_t = next(
        s.t for s in rig.ws.sent if s.kind == "text" and s.payload["type"] == "closed"
    )
    assert rig.ws.closed_at >= closed_t
    assert len(rig.disconnects) == 1


@needs_module
@pytest.mark.asyncio
async def test_ac14_bot_disconnect_timeout(monkeypatch):
    rig = await start_rig(monkeypatch)
    assert rig.mod.CLOSE_WAIT_S == 10.0
    await open_call(rig)
    rig.session.request_disconnect()
    assert await until(rig.task.done)
    sent_disconnect = next(
        s for s in rig.ws.sent if s.kind == "text" and s.payload["type"] == "disconnect"
    )
    assert "closed" not in rig.ws.text_types()
    waited = rig.ws.closed_at - sent_disconnect.t
    assert 10.0 <= waited <= 10.0 + 0.5
    assert len(rig.disconnects) == 1


# ---------------------------------------------------------------- AC-15


def _assert_torn_down(rig: Rig) -> None:
    assert len(rig.disconnects) == 1
    assert rig.stt.stop_calls == 1
    assert rig.stt.cancelled
    assert rig.ws.closed_at is not None


@needs_module
@pytest.mark.asyncio
async def test_ac15_peer_close(monkeypatch):
    rig = await start_rig(monkeypatch)
    await open_call(rig)
    close_at = rig.clock.now()
    await finish(rig)
    closed = next(
        s for s in rig.ws.sent if s.kind == "text" and s.payload["type"] == "closed"
    )
    assert closed.t - close_at < 10.0
    assert rig.ws.closed_at >= closed.t
    _assert_torn_down(rig)


@needs_module
@pytest.mark.asyncio
async def test_ac15_websocket_disconnect(monkeypatch):
    rig = await start_rig(monkeypatch)
    await open_call(rig)
    rig.ws.push_disconnect()
    assert await until(rig.task.done)
    _assert_torn_down(rig)


@needs_module
@pytest.mark.asyncio
async def test_ac15_receive_failure(monkeypatch):
    rig = await start_rig(monkeypatch)
    await open_call(rig)
    rig.ws.incoming.put_nowait(RuntimeError("receive exploded"))
    assert await until(rig.task.done)
    _assert_torn_down(rig)


@needs_module
@pytest.mark.asyncio
async def test_ac15_send_failure(monkeypatch):
    rig = await start_rig(monkeypatch)
    await open_call(rig)
    rig.ws.fail_bytes = True
    queue_audio(rig.session, b"\x01" * 1600)
    assert await until(rig.task.done)
    _assert_torn_down(rig)


@needs_module
@pytest.mark.asyncio
async def test_ac15_no_child_tasks_survive(monkeypatch):
    before = asyncio.all_tasks()
    rig = await start_rig(monkeypatch)
    await open_call(rig)
    await finish(rig)
    await asyncio.sleep(0)
    leftover = [t for t in asyncio.all_tasks() - before if not t.done()]
    assert leftover == []


# ---------------------------------------------------------------- AC-16


@needs_module
def test_ac16_import_does_not_load_twilio():
    projects = Path(__file__).resolve().parent.parent.parent
    code = (
        "import sys, voice_runtime.transports.audiohook_server; "
        "bad = [m for m in sys.modules if m == 'twilio' or m.startswith('twilio.') "
        "or m.endswith('twilio_ws')]; print(bad)"
    )
    env = {**os.environ, "PYTHONPATH": str(projects)}
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert out.stdout.strip() == "[]"
