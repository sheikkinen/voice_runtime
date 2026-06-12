# Judgement: NC-340 STT feed loop must survive a reconnect

**Verdict:** APPROVED WITH REQUIRED REVISIONS
**Judged:** 2026-06-12

## Summary

The diagnosis is correct and verified against source
(`voice_runtime/providers/elevenlabs_stt.py`, v0.1.7): the long-TTS path schedules
a bare `_connect()` (L141) that closes and replaces `self._stt` while the single
`_feed_audio` task keeps sending into the dying socket; 3 consecutive failures
`break` the loop (L226-238) permanently, and **no path recreates `_feed_task`**.
The fix shape — guarantee feeder liveness on reconnect + treat reconnect-window
send failures as transient — is the right cure and removes the permanent-deafness
outcome.

Three things must change before this is frozen. The largest is that AC-5 as
written can land **green for the wrong reason** (`assert_path_not_destination`).
The other two *shrink* the implementation: the concurrency is cooperative
single-loop interleaving, so no lock is needed, and the guard belongs inside
`_connect()` so the two call sites need no separate "routing."

## R-1 (Material): AC-5 must reproduce the race, not the repair

AC-5 says the regression "reproduces the original deafness deterministically." The
RED plan, however, manipulates internals (kill `_feed_task`, assert `_connect`
revives it). That tests the *fix mechanism*, not the *defect*. A test that asserts
"`_ensure_feed_task` recreates a done task" passes trivially once the helper exists
and proves nothing about the actual failure mode — it checks the destination, not
the path.

Required: the witness test must drive the real sequence on one event loop —
`set_speaking(True)`, force `_speaking_since` past the 10 s threshold,
`set_speaking(False)` to trigger the reconnect, with a fake `self._stt` whose
`send()` raises during the close→connect window — and assert that **after the swap
completes, frames put on the inbound queue still reach the new socket**
(`new_stt.send` was awaited with post-reconnect frames). It must fail on `main`
(feeder dead, post-reconnect frames never delivered) and pass after the fix. The
"kill task / revive" assertion may remain as a unit check of `_ensure_feed_task`,
but it is not the AC-5 witness.

## R-2 (Material): the concurrency is single-loop cooperative — no lock

Both `_connect()` and `_feed_audio()` run on `self._loop` (L141 schedules
`_connect` onto the same loop the feeder runs on). The "race" is not preemptive:
`_connect` awaits `self._stt.close()`, yielding control to `_feed_audio`, which
then sends to the closed-but-still-referenced socket. Because the interleaving only
happens at `await` points on a single thread, a plain boolean `_reconnecting` flag
set at the top of `_connect()` and cleared in a `finally` is sufficient and correct.

Required: the FR must state this explicitly and **forbid an `asyncio.Lock` or
threading primitive** for the swap — they are unnecessary and would imply a
concurrency model that does not exist here. Implementation Approach step 2 should
say "boolean flag," not "flag (or `asyncio.Lock`)."

## R-3 (Material): scope the guard to `_connect()` so step 3 disappears

Implementation Approach step 3 ("route long-TTS reconnect through the guarded
path") implies refactoring the `set_speaking` call site. It is simpler and
strictly better to put **both** the `_reconnecting` flag and the
`_ensure_feed_task()` call **inside `_connect()` itself**. Then every caller — the
long-TTS path (L141), the fatal-error path (`_reconnect_after_error`, L191), and
the initial `start()` (L66) — inherits feeder liveness and swap-serialization for
free, with no change to the call sites.

Required: rewrite the approach so the guard lives in `_connect()`. AC-3 (fatal
path recreates feeder) and AC-1 (long-TTS path keeps feeder alive) then collapse to
"`_connect()` always ensures the feeder," which is one mechanism, not two. Setting
the flag in the initial `start()` connect is harmless (no feeder/socket yet) and
need not be special-cased.

## D-1 (Doc-only): a frame is dropped on a failed send — accept it explicitly

`_feed_audio` dequeues a frame (`await inbound.get()`) *before* `send()`. When that
send fails during the swap window, the dequeued frame is gone — the next loop
iteration fetches the *next* frame, it does not retry the failed one. So the fix
cannot promise zero audio loss; it promises **resumption**. The Implementation
Approach pseudocode phrase "retry against the refreshed `self._stt`" wrongly implies
same-frame retry and would require restructuring the dequeue. State instead: a small
number of frames (sub-second) may be lost during the swap; the requirement is that
feeding *resumes*, not that no frame is dropped. Do not let AC-1/AC-5 over-specify
zero-loss.

## D-2 (Doc-only): `_speaking_since` must be settable by the test

The 10 s threshold uses `time.monotonic()`. The witness test (R-1) must reach the
reconnect branch without sleeping 10 s — by assigning `session._speaking_since`
directly (or monkeypatching `time.monotonic`). Note this in the test plan so the
regression stays fast and deterministic.

## What is right and frozen

- The composition diagnosis: correct components (`_connect`, `_feed_audio`,
  `_reconnect_after_error`) joined by a policy that swaps the socket under a running
  feeder and never restores it. Verified against source. Frozen.
- The two-paths table (long-TTS bypasses the hardened routine; neither recreates
  the feeder). Accurate. Frozen.
- Constraints: no silent fallback, preserve bounded retry + `on_error` on
  exhaustion, single socket per call, $0 deterministic test. All correct. Frozen.
- Out-of-scope (threshold configurability; consumer-side short-turn mitigation).
  Correct boundary. Frozen.

## Scope after revisions

One internal change to `_connect()`: a `_reconnecting` boolean guard (set on entry,
cleared in `finally`) plus a `_ensure_feed_task()` call before return; and one
change to `_feed_audio` to skip the failure counter while `_reconnecting` is set.
No new public API, no lock, no call-site refactor. Tests: one race-reproducing
witness (R-1) + unit checks for the transient-failure predicate and the
exhaustion-still-fires-`on_error` guarantee.
