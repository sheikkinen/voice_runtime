"""VR-015 GooglePersistentStt speech-activity pulse.

While Google reports active speech (SPEECH_ACTIVITY_BEGIN until END, a final,
stream end, stop() or the cap), on_recognizing("") repeats every 0.25 s so a
consumer's silence timer does not expire mid-utterance. Timings are virtual
seconds relative to the BEGIN at t=0, on the VR-013 virtual-time harness.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

pytest.importorskip(
    "google.cloud.speech_v2", reason="optional google extra not installed"
)

from google.api_core import exceptions as gexc  # noqa: E402 — after skip

from tests.test_vr013_google_stt import (  # noqa: E402 — after skip
    Event,
    _clean_env,  # noqa: F401 — autouse fixture, registered by import
    event,
    final,
    interim,
    logs,  # noqa: F401 — fixture, registered by import
    provider_module,
    sdk,  # noqa: F401 — fixture, registered by import
    start,
    stop,
    vt,  # noqa: F401 — fixture, registered by import
)

pytestmark = pytest.mark.req("VR-015")

BEGIN = Event.SPEECH_ACTIVITY_BEGIN
END = Event.SPEECH_ACTIVITY_END
QUARTERS_TO_2S = [0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
CAP_WARNING = "Google STT speech pulse cap reached after 30s"


class Timeline:
    """Consumer callbacks stamped with virtual time relative to t0."""

    def __init__(self, stt) -> None:
        self.loop = asyncio.get_running_loop()
        self.t0 = self.loop.time()
        self.recognizing: list[tuple[float, str]] = []
        self.committed: list[tuple[float, str]] = []
        self.errors: list[str] = []
        stt.on_recognizing = lambda text: self.recognizing.append((self.at(), text))
        stt.on_committed = lambda text: self.committed.append((self.at(), text))
        stt.on_error = self.errors.append

    def at(self) -> float:
        return round(self.loop.time() - self.t0, 6)

    def mark(self) -> None:
        self.t0 = self.loop.time()

    async def until(self, t: float) -> None:
        await asyncio.sleep(self.t0 + t - self.loop.time())

    def times(self) -> list[float]:
        return [t for t, _ in self.recognizing]


async def session(sdk):
    stt, _, queue = await start(sdk)
    return stt, Timeline(stt), queue


async def begin(sdk, tl: Timeline) -> None:
    tl.mark()
    sdk.calls[-1].push(event(BEGIN))


def run(vt, scenario):
    vt.run_until_complete(scenario())


def live_pulse_producers(stt) -> int:
    """Owned tasks besides the feeder and the session owner."""
    return sum(1 for t in stt._owned_futures if not t.done()) - 2


# ── AC-02: cadence ──────────────────────────────────────────────────────


def test_begin_then_pulse_every_quarter_second(vt, sdk):
    async def scenario():
        stt, tl, _ = await session(sdk)
        await begin(sdk, tl)
        await tl.until(2.001)  # drain everything due through t=2.0
        assert tl.recognizing == [(t, "") for t in QUARTERS_TO_2S]
        await stop(stt)

    run(vt, scenario)


# ── AC-03: termination by END or a final ────────────────────────────────


def test_speech_end_stops_pulse(vt, sdk):
    async def scenario():
        stt, tl, _ = await session(sdk)
        await begin(sdk, tl)
        await tl.until(0.6)
        sdk.calls[-1].push(event(END))
        await tl.until(2.6)
        assert tl.times() == [0.0, 0.25, 0.5]
        await stop(stt)

    run(vt, scenario)


def _committed_final(stt, sdk, tl):
    sdk.calls[-1].push(final("Hei."))


def _empty_final(stt, sdk, tl):
    sdk.calls[-1].push(final("   "))


@pytest.mark.parametrize(
    ("push_final", "steps", "expected_times", "expected_commits"),
    [
        (_committed_final, [], [0.0, 0.25, 0.5], ["Hei."]),
        (_empty_final, [], [0.0, 0.25, 0.5], []),
        # Speaking from 0.3: the 0.5 pulse is suppressed, the final is discarded.
        (_committed_final, [(0.3, True)], [0.0, 0.25], []),
        # Speaking 0.3-0.4: the final at 0.6 falls in the echo window.
        (_committed_final, [(0.3, True), (0.4, False)], [0.0, 0.25, 0.5], []),
    ],
    ids=["committed", "empty", "speaking_suppressed", "echo_discarded"],
)
def test_final_without_end_stops_pulse(
    vt, sdk, push_final, steps, expected_times, expected_commits
):
    async def scenario():
        stt, tl, _ = await session(sdk)
        await begin(sdk, tl)
        for at, speaking in steps:
            await tl.until(at)
            stt.set_speaking(speaking)
        await tl.until(0.6)
        push_final(stt, sdk, tl)
        await tl.until(2.6)
        assert tl.times() == expected_times
        assert [text for _, text in tl.committed] == expected_commits
        await stop(stt)

    run(vt, scenario)


# ── AC-04: synthetic B-1 analogue (not an exact replay) ─────────────────


def test_continuation_after_final_is_pulsed_until_speech_end(vt, sdk):
    async def scenario():
        stt, tl, _ = await session(sdk)
        sdk.calls[-1].push(final("Kyllä."))
        await asyncio.sleep(0.05)
        await begin(sdk, tl)
        await tl.until(1.6)
        sdk.calls[-1].push(event(END))
        await tl.until(2.1)
        sdk.calls[-1].push(final("juuri reseptin uusimisesta on kyse."))
        await tl.until(3.0)
        assert tl.times() == [0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5]
        assert [text for _, text in tl.committed] == [
            "Kyllä.",
            "juuri reseptin uusimisesta on kyse.",
        ]
        assert tl.committed[1][0] == 2.1
        await stop(stt)

    run(vt, scenario)


# ── AC-05: cap ──────────────────────────────────────────────────────────


def test_pulse_stops_at_cap_with_one_warning(vt, sdk, logs):
    async def scenario():
        stt, tl, _ = await session(sdk)
        await begin(sdk, tl)
        await tl.until(32.0)
        times = tl.times()
        assert len(times) == 120  # t=0 plus 0.25 ... 29.75
        assert times[-1] == 29.75
        assert live_pulse_producers(stt) == 0
        await stop(stt)

    run(vt, scenario)
    warnings = [
        r
        for r in logs.records
        if r.levelno == logging.WARNING and "speech pulse cap" in r.getMessage()
    ]
    assert [r.getMessage() for r in warnings] == [CAP_WARNING]


def test_cap_constants():
    module = provider_module()
    assert module.SPEECH_PULSE_INTERVAL_S == 0.25
    assert module.SPEECH_PULSE_MAX_S == 30.0


# ── AC-06: second BEGIN restarts the periodic schedule ──────────────────


def test_second_begin_restarts_single_schedule(vt, sdk):
    async def scenario():
        stt, tl, _ = await session(sdk)
        await begin(sdk, tl)
        await tl.until(0.1)
        sdk.calls[-1].push(event(BEGIN))
        await tl.until(0.9)
        assert tl.times() == [0.0, 0.1, 0.35, 0.6, 0.85]
        assert live_pulse_producers(stt) == 1
        await stop(stt)

    run(vt, scenario)


# ── AC-07: speaking, echo window, interim text ──────────────────────────


def test_speaking_suppresses_then_pulses_resume_inside_echo_window(vt, sdk):
    async def scenario():
        stt, tl, _ = await session(sdk)
        await begin(sdk, tl)
        await tl.until(0.3)
        stt.set_speaking(True)
        await tl.until(0.9)
        assert live_pulse_producers(stt) == 1
        await tl.until(1.0)
        stt.set_speaking(False)  # echo window until 1.4
        await tl.until(1.6)
        assert tl.times() == [0.0, 0.25, 1.25, 1.5]
        assert live_pulse_producers(stt) == 1
        await stop(stt)

    run(vt, scenario)


def test_interim_text_forwarded_and_pulse_continues(vt, sdk):
    async def scenario():
        stt, tl, _ = await session(sdk)
        await begin(sdk, tl)
        await tl.until(0.6)
        sdk.calls[-1].push(interim("Hei"))
        await tl.until(1.1)
        assert tl.recognizing == [
            (0.0, ""),
            (0.25, ""),
            (0.5, ""),
            (0.6, "Hei"),
            (0.75, ""),
            (1.0, ""),
        ]
        assert live_pulse_producers(stt) == 1
        await stop(stt)

    run(vt, scenario)


def test_no_pulse_without_begin(vt, sdk):
    async def scenario():
        stt, tl, _ = await session(sdk)
        await tl.until(2.0)
        assert tl.recognizing == []
        await stop(stt)

    run(vt, scenario)


# ── AC-08: lifecycle ────────────────────────────────────────────────────


def test_stop_during_pulse_leaves_nothing_pending(vt, sdk):
    async def scenario():
        stt, tl, _ = await session(sdk)
        await begin(sdk, tl)
        await tl.until(0.6)
        await stop(stt)  # asserts every owned task finished
        await tl.until(3.0)
        assert tl.times() == [0.0, 0.25, 0.5]

    run(vt, scenario)


def test_input_eof_ends_pulse(vt, sdk):
    async def scenario():
        stt, tl, queue = await session(sdk)
        await begin(sdk, tl)
        await tl.until(0.6)
        queue.put_nowait(None)
        await tl.until(3.0)
        assert tl.times() == [0.0, 0.25, 0.5]
        assert not stt._owned_futures
        assert tl.errors == []
        await stop(stt)

    run(vt, scenario)


def test_terminal_failure_ends_pulse(vt, sdk):
    async def scenario():
        stt, tl, _ = await session(sdk)
        await begin(sdk, tl)
        await tl.until(0.6)
        sdk.calls[-1].push(gexc.PermissionDenied("denied"))
        await tl.until(3.0)
        assert tl.times() == [0.0, 0.25, 0.5]
        assert tl.errors == ["google_stt_permission_denied"]
        await stop(stt)

    run(vt, scenario)


def test_retry_ends_pulse_until_begin_on_new_stream(vt, sdk):
    async def scenario():
        stt, tl, _ = await session(sdk)
        await begin(sdk, tl)
        await tl.until(0.6)
        sdk.calls[-1].push(gexc.ServiceUnavailable("blip"))
        await tl.until(3.0)  # 1 s backoff, replacement open at ~1.6
        assert len(sdk.calls) == 2
        assert tl.times() == [0.0, 0.25, 0.5]
        sdk.calls[-1].push(event(BEGIN))
        await tl.until(3.6)
        assert tl.times() == [0.0, 0.25, 0.5, 3.0, 3.25, 3.5]
        assert live_pulse_producers(stt) == 1
        await stop(stt)

    run(vt, scenario)


def test_forced_rotation_ends_pulse_until_begin_on_new_stream(vt, sdk):
    async def scenario():
        stt, tl, _ = await session(sdk)
        opened = sdk.calls[0].opened_at
        await asyncio.sleep(opened + 289.6 - tl.loop.time())
        await begin(sdk, tl)  # forced rotation at t=0.4
        await tl.until(2.0)
        assert len(sdk.calls) == 2
        assert tl.times() == [0.0, 0.25]
        sdk.calls[-1].push(event(BEGIN))
        await tl.until(2.6)
        assert tl.times() == [0.0, 0.25, 2.0, 2.25, 2.5]
        assert live_pulse_producers(stt) == 1
        await stop(stt)

    run(vt, scenario)
