"""NC-236: send_mark_and_wait must not let concurrent callers with the same
logical mark name resolve each other's waits.

Reproduces the farewell cut-off bug observed on call CAa08160... where
ack's `send_mark_and_wait("tts_complete")` and farewell's
`send_mark_and_wait("tts_complete")` overlapped; the first mark echo
resolved farewell prematurely, truncating 8.16s of audio to 0.8s.

See: projects/ninchat_voice/feature-requests/NC-236-unique-mark-names-prevent-farewell-cutoff.md
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from voice_runtime.session import VoiceSession


@pytest.mark.req("REQ-NC-236")
def test_concurrent_send_mark_and_wait_same_name_does_not_cross_resolve():
    """Two concurrent waiters on the same logical mark name must each block
    until *their own* audio finishes. A single mark echo from the transport
    must not release a waiter that started *after* the mark-emitting audio.

    Bug (pre-fix): `_pending_marks[mark_name] = event` overwrites; the first
    transport echo sets the second caller's event; the first caller times out
    while the second returns prematurely with wrong elapsed time.
    """
    session = VoiceSession()
    loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
    loop_thread.start()
    # A4: use the public set_loop contract, not direct _loop mutation.
    session.set_loop(loop)

    # Drain the mark queue as Twilio would, and simulate two distinct echoes
    # with realistic timing. Key invariant: the transport echoes marks in
    # FIFO order, once per mark it was asked to emit.
    echoed: list[str] = []
    stop = threading.Event()

    def transport():
        # Pull two marks off the outbound queue, echo them back with
        # realistic per-audio delays: 0.2s for ack, then 1.0s for farewell.
        delays = [0.2, 1.0]
        for delay in delays:
            if stop.is_set():
                return
            future = asyncio.run_coroutine_threadsafe(
                session._mark_queue.get(), loop
            )
            try:
                mark = future.result(timeout=5.0)
            except Exception:
                return
            echoed.append(mark)
            time.sleep(delay)
            session.signal_mark_received(mark)

    transport_thread = threading.Thread(target=transport, daemon=True)
    transport_thread.start()

    # Caller A (ack): starts first, expects to block ~0.2s.
    # Caller B (farewell): starts ~0.01s later, expects to block ~1.2s total
    # (0.2s until ack echo + 1.0s until farewell echo).
    results: dict[str, float] = {}

    def caller(label: str):
        t0 = time.time()
        session.send_mark_and_wait("tts_complete", timeout=5.0)
        results[label] = time.time() - t0

    a = threading.Thread(target=caller, args=("ack",), daemon=True)
    b = threading.Thread(target=caller, args=("farewell",), daemon=True)
    a.start()
    time.sleep(0.01)
    b.start()
    a.join(timeout=5.0)
    b.join(timeout=5.0)

    stop.set()
    loop.call_soon_threadsafe(loop.stop)
    loop_thread.join(timeout=1.0)

    # Both callers must have returned (neither timed out).
    assert "ack" in results, (
        "ack caller did not return — bug: its event was overwritten by farewell "
        "and never set by the single echo that resolved farewell's entry."
    )
    assert "farewell" in results, "farewell caller did not return"

    # Ack should finish around 0.2s (the first echo).
    assert 0.15 < results["ack"] < 0.5, (
        f"ack returned in {results['ack']:.2f}s; expected ~0.2s "
        "(pre-fix: ack is overwritten and times out, or returns with wrong timing)"
    )

    # Farewell must wait for the *second* echo (ack's 0.2s + its own 1.0s ≈ 1.2s).
    # Pre-fix, farewell would return at ~0.2s when the first echo arrives and
    # releases the last-written event entry — which is farewell's.
    assert results["farewell"] > 0.9, (
        f"farewell returned in {results['farewell']:.2f}s; expected > 0.9s. "
        "BUG: first mark echo resolved farewell's event prematurely "
        "(shared mark_name → overwritten _pending_marks entry)."
    )
