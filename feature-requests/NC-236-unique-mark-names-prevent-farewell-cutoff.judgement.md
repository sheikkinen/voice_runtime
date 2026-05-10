# NC-236 Judgement: Unique Mark Names Per Speak

**Verdict:** APPROVED with 4 amendments.

**Date:** 2026-04-20
**Reviewed against:** `projects/ninchat_voice/feature-requests/NC-236-unique-mark-names-prevent-farewell-cutoff.md`, `projects/voice_runtime/session.py:172-203`, `projects/voice_runtime/tests/test_session.py` (TestMarkSync, TestResetMarkSafety), `projects/voice_runtime/tests/test_elevenlabs_tts.py:66`, `projects/ninchat_voice/services/bridge_handlers.py::_on_speak`, `projects/ninchat_voice/services/tts.py::_speak_from_file`.

## Assessment

### Root cause is correctly identified

`session.py:180-194` matches the FR's diagnosis verbatim:

```python
event = threading.Event()
self._pending_marks[mark_name] = event          # ← plain overwrite
asyncio.run_coroutine_threadsafe(self._mark_queue.put(mark_name), self._loop)
if not event.wait(timeout=timeout):
    self._pending_marks.pop(mark_name, None)
    ...
if mark_name in self._pending_marks:
    del self._pending_marks[mark_name]
```

Two concurrent callers with identical `mark_name` silently share a key; the second write wins; the first echo resolves the wrong waiter. The "Received unknown mark: tts_complete" log the FR cites is the signature of the *second* echo arriving after the single dict entry has been deleted — an exact match for the described mode.

### Fix is the right shape

Minting a unique suffix inside `send_mark_and_wait` keeps the logical-label API intact, localises the change to one function, and is semantically isomorphic to Twilio's opaque-string mark contract. The alternative (FIFO queue of events per logical name) solves the same problem but requires thinking about whether Twilio's mark echoes are strictly FIFO across overlapping audio streams — the unique-key approach sidesteps the question. Prefer the FR's approach.

### NC-234 vs NC-236 framing is accurate

NC-234 renamed the *FSM event* (`ack_speak_done` vs `speak_done`) so the wrong transition would not consume a right event. NC-236 fixes the *transport mark waiter* so the wrong `threading.Event` is not resolved by a right mark echo. These are two distinct boundaries; both needed fixing. The FR's layering claim holds.

## Amendments (required)

### A1: Update existing tests in the same commit as the GREEN fix

The FR flags the test impact (Risk row 2) but does not commit to a concrete plan. Four existing tests currently assert the exact string `"tts_complete"` against transport-layer state:

- `tests/test_session.py:163` — `assert mark == "tts_complete"` (pulled from `_mark_queue` via `get_pending_mark()`). This will break: post-fix the queued value is `tts_complete__xxxxxxxx`.
- `tests/test_session.py:335` and `:374` (TestResetMarkSafety Bugs 1 and 2) — these do not read from the queue; they only pass the logical name to `send_mark_and_wait` and exercise `reset()`. Should continue passing if the `_pending_marks.pop(unique, …)` is in a `finally` (see A3).
- `tests/test_elevenlabs_tts.py:66` — asserts `session.send_mark_and_wait.assert_called_once_with("tts_complete", timeout=30.0)`. Safe: callers still pass the logical label.

Required action:
- Modify `test_mark_roundtrip` to assert the queued mark *starts with* `"tts_complete__"` (or `startswith("tts_complete")` with an explicit length check), and echo back the exact string it received, not a hard-coded one.
- Verify no other transport-layer test asserts exact-equality on a mark pulled from the queue or inspected in `_pending_marks`.
- Both the test update and the fix land in the GREEN commit; the RED commit contains only the new NC-236 test.

Reason:
- Leaving a broken test for a follow-up commit breaks `git bisect` across the fix range and contradicts Scripture command 7 (GREEN means the whole suite is green, not just the new test).

### A2: Tighten collision handling explicitly, not conditionally

The FR defers collision handling to a *"wrap the assignment in a `while key in _pending_marks:` loop; adds two lines"* comment inside the Risks table. This is the correct fix; lift it into the design, not the footnote.

Required action:
- The implementation must generate a fresh UUID if the candidate key already exists in `_pending_marks`:

```python
while True:
    unique = f"{mark_name}__{uuid.uuid4().hex[:8]}"
    if unique not in self._pending_marks:
        break
self._pending_marks[unique] = event
```

- Birthday-bound reminder: at 8 hex chars the first collision becomes probable around ~65k concurrent marks on one session; the while-loop cost stays ~O(1) at realistic call volumes (≤10 outstanding marks).

Reason:
- Scripture: *Thou shalt bear witness of thy errors.* Silently accepting a 2⁻³² rare path when the remediation is two lines is inconsistent with normalise-at-the-boundary. Make the guarantee unconditional.

### A3: Fix the stale-write in the existing non-timeout path

`session.py:193-194` has a post-wait `if mark_name in self._pending_marks: del self._pending_marks[mark_name]` that runs only on the success path. The FR's proposed code uses a `finally` block — good — but the Acceptance Criteria say "*`_pending_marks.pop(unique, None)` is in a `finally` block*" without requiring the old fallback block be deleted.

Required action:
- The rewrite must remove lines 193–194 entirely. The `finally` pops the unique key on both success and timeout; the separate success-path delete is dead code after the fix.
- Keep `reset()`'s iterate-and-set-then-clear logic in `session.py:238-252` unchanged — it continues to unblock all waiters on call teardown regardless of key naming (`reset()` iterates `.values()`, no key inspection).

Reason:
- Leaving both the `finally` and the legacy trailing delete creates two deletion paths for the same key. With unique keys, the trailing delete targets a name that no longer exists; it's dead but not harmless — a future reader will misunderstand the invariant.

### A4: Reproduction test needs one hardening

The condemning test in the FR is well-targeted but has one fragility: `session._loop = loop` bypasses `set_loop()`, and `session._mark_queue` is accessed directly. Existing transport-layer tests use `set_loop(loop)` (e.g. `test_mark_roundtrip`), which matches the public contract and initialises any lazy fields.

Required action:
- Replace `session._loop = loop` with `session.set_loop(loop)` in the test.
- Keep the two distinct delays (0.2s / 1.0s) and the wide assertion windows (`0.15 < ack < 0.5`, `farewell > 0.9`). Do not tighten.
- After implementation, run the test 20 times locally (`pytest projects/voice_runtime/tests/test_nc236_unique_mark_names.py -q --count=20` via `pytest-repeat`, or a shell loop) and record the result (min/max/p95 elapsed times) in the PR description. This is not a gating acceptance item — a sanity check for CI flakiness.

Reason:
- Direct `_loop` mutation skips `set_loop`'s intended initialisation hooks and risks future regressions if `set_loop` grows side effects.

## Non-blocking observations

- **Existing handler concurrency assumption.** The FR claims `_on_speak` handlers run concurrently. Confirmed by the production timeline (`+100.07` ack start, `+103.11` farewell start while ack audio still playing). The bridge socket handler is dispatched on whatever scheduling the underlying server provides; the fix is correct regardless of whether handlers are threaded or coroutine-scheduled, because the shared-key bug manifests under any interleaving.
- **Why not serialise `_on_speak`?** The FR correctly rejects this in Non-goals. Serialising would preserve NC-229's ack-concurrent-with-LLM win at the cost of the fastest-acceptable speak dispatch path. Unique marks are strictly the better fix.
- **Audit follow-up (FR's own Seed).** The pattern `dict[str, Event]` with a shared logical key appears in `session.py` only for `_pending_marks`. A quick grep (`rg 'dict\[str,\s*threading\.Event\]' projects/`) shows no other occurrences. The Seed stands for the broader repo, but the immediate worry is contained.
- **Changelog fragment.** FR says `type: fix`, scope `voice_runtime`. Correct. Include `req:` referencing whichever requirement captures transport-layer correctness; if none, leave unset per FR-247.

## Authority

**Authority granted after A1–A4 are incorporated into the FR and implementation.** RED/GREEN commit separation is mandatory (Scripture command 7).

Post-merge smoke (manual real call with full farewell audio, no "Received unknown mark" warnings) is a valid acceptance gate; do not skip it — this is the exact production signal that condemned the original code.
