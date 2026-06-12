# NC-340 — STT feed loop must survive a reconnect

**Status:** Enforced (2026-06-12) — RED `ecb2db9`, GREEN follows; R-1..R-3, D-1..D-2 folded below; see `NC-340-stt-feed-survives-reconnect.judgement.md`
**Judged:** 2026-06-12
**Repo:** sheikkinen/voice_runtime
**Component:** `voice_runtime/providers/elevenlabs_stt.py` — `PersistentSttSession`
**Version observed:** 0.1.7
**Severity:** High — call continues but STT goes permanently deaf for the rest of the call
**First seen:** HP-31 persona-caller live run, 2026-06-12 (call_sid `CA97178baae5c96c7e203003999c921e61`)
**Source report:** `tt-test-plan/docs/known-bug-voice-runtime-stt-deafness.md`

---

## Problem

When an agent TTS turn lasts longer than `_RECONNECT_AFTER_SPEAKING_S` (10.0 s),
`set_speaking(False)` schedules a raw `_connect()` to re-open the Scribe
WebSocket. That reconnect races against the single, long-lived `_feed_audio`
task:

1. `_connect()` calls `self._stt.close()` and replaces `self._stt`.
2. Concurrently `_feed_audio` is calling `self._stt.send(...)` against the
   socket being torn down, so the sends raise.
3. After `_MAX_CONSECUTIVE_SEND_FAILURES` (3) failures `_feed_audio` fires
   `on_error` and **`break`s permanently**. The `stt_feed` task completes.
4. `_connect()` finishes with a healthy socket — but **no reconnect path
   recreates `_feed_task`**, so audio is never fed again. Every subsequent
   `listen` returns `commits=0`, and inbound frames pile up unconsumed
   (growing "Cleared N stale frames" count is the tell).

This is a **composition defect**: `_connect()`, `_feed_audio()`, and
`_reconnect_after_error()` each satisfy their own contract, but the policy
connecting them allows a socket swap to fire underneath a running feeder, and
no path guarantees feeder liveness.

### Two reconnect paths — only the naive one is reached on long TTS

| Path | Trigger | Drains stale frames | Backoff | Bounded | Restarts feeder |
|------|---------|---------------------|---------|---------|-----------------|
| `_reconnect_after_error()` | fatal STT error | yes | exponential+jitter | yes (3) | **no** |
| raw `_connect()` (via `set_speaking`) | long TTS (>10 s) | no | no | no | **no** |

The long-TTS path bypasses the hardened routine entirely, and **neither** path
recreates `_feed_task`. The feeder is an unrepaired single point of failure.

### Why scripted tests miss it

The bug is a property of a physical timing phenomenon — turn duration crossing
the 10 s threshold. Scripted answerers emit fixed short utterances and never
cross it; only a natural variable-length caller (persona LLM) reproduces it.

---

## Objective

After any STT reconnect (long-TTS or fatal-error), audio feeding resumes
automatically, so `listen` windows continue to commit transcripts for the rest
of the call. A reconnect must never leave the session permanently deaf.

---

## Constraints

- No silent fallbacks. A genuinely unrecoverable STT must still surface via
  `on_error` (preserve the bounded-retry semantics of `_reconnect_after_error`).
- No new public API on `PersistentSttSession`; fix is internal to the provider.
- Honour existing constants (`_MAX_CONSECUTIVE_SEND_FAILURES`,
  `_MAX_RECONNECT_ATTEMPTS`, backoff constants) — do not weaken the
  fatal-error bound.
- Single Scribe socket per call lifetime preserved (NC-130); the swap must be
  atomic with respect to the feeder, not a second concurrent socket.
- Deterministic test at $0 — no live ElevenLabs connection in the regression.
- **(R-2) Single-loop cooperative concurrency.** `_connect()` and `_feed_audio()`
  both run on `self._loop`; the "race" is interleaving at `await` points on one
  thread, not preemption. The swap guard MUST be a plain boolean flag — **no
  `asyncio.Lock`, no threading primitive** (they imply a concurrency model that
  does not exist here).
- **(D-1) Resumption, not zero-loss.** `_feed_audio` dequeues a frame before
  `send()`, so a frame whose send fails during the swap window is dropped (the
  next iteration fetches the next frame). The guarantee is that feeding *resumes*
  after the swap; sub-second frame loss during the window is accepted, not a
  defect. Acceptance criteria must not demand zero-loss / same-frame retry.

---

## Acceptance Criteria

1. **Feeder liveness after long-TTS reconnect.** Given a feed loop running and a
   `set_speaking(False)` that triggers the >10 s reconnect, after `_connect()`
   completes `self._feed_task` is alive (`not done()`) and subsequent frames
   reach the new `self._stt`.
2. **Send failures during a deliberate reconnect are transient.** Send failures
   that occur while a reconnect is in progress do not count toward
   `_MAX_CONSECUTIVE_SEND_FAILURES`, so a routine socket swap never escalates to
   permanent `break`.
3. **Fatal-error path also recreates the feeder.** `_reconnect_after_error()`
   ensures `_feed_task` is alive after a successful reconnect; the bounded-retry
   + `on_error`-on-exhaustion behaviour is unchanged.
4. **Genuine unrecoverable failure still raised.** When reconnects are exhausted,
   `on_error` fires exactly as today (no swallowed error, no infinite loop).
5. **(R-1) Regression test reproduces the *race*, not the repair.** The witness
   drives the real sequence on one event loop — `set_speaking(True)`, force
   `_speaking_since` past the 10 s threshold (D-2: assign it directly or
   monkeypatch `time.monotonic`, do not sleep), `set_speaking(False)` to trigger
   the reconnect, with a fake `self._stt` whose `send()` raises during the
   close→connect window — and asserts that **after the swap, frames put on the
   inbound queue still reach the new socket** (post-reconnect frames are sent to
   `new_stt`). It fails on `main` (feeder dead, post-reconnect frames never
   delivered) and passes after the fix. Asserting feeder liveness alone
   (`not _feed_task.done()`) is necessary but **not** the AC-5 witness — that is
   `assert_path_not_destination`. The "kill task / `_ensure_feed_task` revives it"
   check may remain as a separate unit test.

---

## Implementation Approach (TDD)

### RED — failing tests first

Add `tests/test_stt_feed_survives_reconnect.py`, tagged
`@pytest.mark.req("NC-340")`, exercising the seam with a fake `self._stt` whose
`send()` raises during a simulated swap window (follow the AsyncMock pattern in
`tests/test_stt_lifecycle.py` and `tests/test_nc260_gap_c_stt_fatal_errors.py`):

- **`test_long_tts_reconnect_resumes_feeding` (AC-5 witness, R-1)** — drive
  `set_speaking(True)`, set `_speaking_since` past 10 s (D-2), `set_speaking(False)`;
  with a fake `self._stt.send()` that raises during the close→connect window,
  assert that frames enqueued *after* the swap are sent to the **new** socket.
  Fails on `main`.
- `test_send_failures_during_reconnect_are_transient` — raise on `send()` only
  while the `_reconnecting` flag is set; assert the loop does not `break` and
  resumes (counter not incremented for those failures).
- `test_fatal_error_reconnect_recreates_feeder` — kill the feeder, trigger
  `_on_error` with a `_FATAL_ERRORS` type; assert feeder alive afterwards.
- `test_reconnect_exhaustion_still_fires_on_error` — force `_connect()` to keep
  failing; assert `on_error` fires after `_MAX_RECONNECT_ATTEMPTS` and the loop
  does not spin.
- `test_ensure_feed_task_unit` (not the AC-5 witness) — call `_ensure_feed_task`
  with a `done()` feeder; assert a fresh task is created.

### GREEN — minimal change

1. **Guarantee feeder liveness in one place.** Add an internal helper, e.g.
   `_ensure_feed_task()`, that recreates `_feed_task` if `None` or `done()`:
   ```python
   def _ensure_feed_task(self) -> None:
       if self._inbound_queue is not None and (
           self._feed_task is None or self._feed_task.done()
       ):
           self._feed_task = asyncio.create_task(
               self._feed_audio(self._inbound_queue), name="stt_feed"
           )
   ```
   Call it **inside `_connect()`** before return (R-3) so every caller — long-TTS
   (L141), fatal-error (`_reconnect_after_error`), and initial `start()` — restores
   the feeder for free. Calling it during the initial connect is harmless (no
   feeder/socket yet) and need not be special-cased.
2. **Serialize the socket swap against the feeder (R-2).** Set a plain boolean
   `self._reconnecting = True` at the top of `_connect()` and clear it in a
   `finally`. In `_feed_audio`, a `send()` failure while `self._reconnecting` is
   set is transient — do **not** increment `consecutive_failures`; `await
   asyncio.sleep(0.1)` and continue the loop (the next iteration fetches the next
   frame against the refreshed `self._stt`; the in-flight frame is dropped per
   D-1). No lock.
3. **No call-site change (R-3).** Because the flag + `_ensure_feed_task()` live
   inside `_connect()`, the long-TTS path keeps its existing
   `run_coroutine_threadsafe(self._connect(), self._loop)` and inherits both
   guarantees. AC-1 and AC-3 collapse to one mechanism: "`_connect()` always
   ensures the feeder."

### REFACTOR

None required beyond the above — the guard living in `_connect()` already unifies
both reconnect entry points. Keep the fatal-error backoff/bound (`_reconnect_after_error`)
and the long-TTS trigger (`set_speaking`) as the two distinct *entry* conditions;
only the *connect* primitive they share is hardened.

---

## Out of Scope

- Making `_RECONNECT_AFTER_SPEAKING_S` configurable (separate concern; the fix
  must hold at any threshold).
- Consumer-side mitigations in `ninchat_voice` / `tt-test-plan` (keeping persona
  turns short is a workaround, not the fix).

---

## Downstream Mitigation (until released)

`tt-test-plan` enforces short persona-caller turns (< 10 s of speech) in
`app/graphs/prompts/caller_persona.yaml` so the long-TTS reconnect never fires.
This FR removes the underlying race so the mitigation is no longer load-bearing.

---

## Implementation Status (2026-06-12)

Enforced in `voice_runtime/providers/elevenlabs_stt.py`:

- `__init__`: added `self._reconnecting = False`.
- `_connect()`: wraps the close→connect swap in `try/finally` setting
  `_reconnecting`, and calls a new `_ensure_feed_task()` before returning, so
  every caller (long-TTS, fatal-error, initial `start()`) restores a live feeder
  (R-3 — guard in one place, no call-site changes; R-2 — boolean flag, no lock).
- `_feed_audio()`: a `send()` failure while `_reconnecting` is set is transient —
  `await asyncio.sleep(0.1)` and `continue`, not counted toward
  `_MAX_CONSECUTIVE_SEND_FAILURES` (D-1 — resumption, the in-flight frame is
  dropped; not zero-loss).

Tests (`tests/test_nc340_stt_feed_survives_reconnect.py`, `@pytest.mark.req("NC-340")`):

- `test_long_tts_reconnect_resumes_feeding` — AC-5 witness (R-1): reproduces the
  race; post-reconnect frames reach the new socket. RED on `main`, GREEN after.
- `test_send_failures_during_reconnect_do_not_break_feeder` — AC-2.
- `test_fatal_error_reconnect_recreates_feeder` — AC-3.
- `test_reconnect_exhaustion_still_fires_on_error` — AC-4 guard (unchanged
  bounded-retry + `on_error` contract).

RED commit `ecb2db9` (3 failing, 1 guard passing). After fix: 4 passed; full
suite 291 passed, 1 skipped, no regression.
