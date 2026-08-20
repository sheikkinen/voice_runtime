# Feature Request: VR-005 STT session must own every task it spawns (duplicate feed task + reconnect orphans)

**Priority:** HIGH (sole remaining cause of harness `No crash` failures on happy-path runs; also loses the first caller utterance)
**Type:** Bug fix
**Status:** Draft
**Effort:** 0.5 day
**Requested:** 2026-08-20
**Downstream:** csap-black-box-tests FR-003/FR-005 (harness-side teardown fixed there; this is the remaining upstream half), csap-black `docs/known-bug-voice-runtime-duplicate-feed-task.md` (open since 0.1.8), csap case studies 2026-08-20-hp35-CA19b1f876… (Finding 3) and 2026-08-20-hp93-CAc8b7f44b… (residual section)

## Problem

`PersistentSttSession` (providers/elevenlabs_stt.py, 0.1.12) spawns tasks
it does not own, so `stop()` cannot tear them down. Three defects in one
family, all field-witnessed on 2026-08-20:

### D-A: initial-start duplicate feed task (the known bug, now source-pinned)

`start()` (line 59) awaits `_connect()`, whose last statement is
`_ensure_feed_task()` (line 123) — `self._feed_task` is None at initial
start, so **task A** is created (line 133). `start()` then executes its
own `self._feed_task = asyncio.create_task(...)` (line 69) — **task B**
overwrites the only reference to task A. Task A is unreachable forever:

- Two feeders race one inbound queue → the intermittent lost first
  utterance / `commits=0` on the first listen (known-bug doc, first seen
  2026-06-18 at 0.1.8).
- `stop()` cancels only task B; task A survives to loop close →
  `Task was destroyed but it is pending!` + `RuntimeError: Event loop is
  closed` (witnessed 2026-08-20 14:20 local, HP-35 run, AFTER the
  harness-side teardown fix landed — the harness did everything right
  and still crashed).

### D-B: reconnect-spawned tasks unowned

The long-TTS/fatal-error reconnect path replaces the Scribe connection;
the replacement `_connect` task and the SDK's message-handler/keepalive
tasks are not rooted anywhere `stop()` reaches (witnessed 2026-08-20
12:21 local, HP-93 run: orphaned `_connect` + `_feed_audio` after
"Scribe reconnect: TTS lasted 46.9s").

### D-C: `stop()` cancels without awaiting (hardening)

`stop()` calls `self._feed_task.cancel()` and never awaits the cancelled
task. csap-black FR-005 refuted this as the crash *cause* (the
`await self._stt.close()` happens to yield the needed loop cycle), but
it is only safe by accident — remove the `_stt.close()` await and the
race returns.

## Deliverable

Task-ownership contract for the session: every task the session spawns
is reachable from the session, and `stop()` cancels **and awaits** all
of them.

1. Fix D-A: `start()` must not double-create — either `start()` relies
   on `_connect()`'s `_ensure_feed_task()` (delete line 69's create), or
   `_connect()` must not pre-create on initial start. One feeder per
   session, ever.
2. Fix D-B: track reconnect-spawned tasks (a task set or re-rooted
   attributes); `stop()` tears down the *current* connection's tasks,
   whatever generation they are.
3. Fix D-C: `stop()` awaits cancelled tasks with
   `gather(*tasks, return_exceptions=True)` before returning, so
   "PersistentSttSession stopped" in the log MEANS all session tasks are
   done (signal = state, not intent — the csap NC-445/446 lesson).
4. Verify the same contract in `providers/azure_stt.py` — the identical
   orphan fired there under `STT_PROVIDER=azure` (csap-black
   teardown-selftest shakedown, azure_stt.py:138); fix if the same
   pattern exists.

## Red/green witness

- RED first: a unit test that starts a session against a fake queue with
  a stubbed connection **whose `_connect` calls `_ensure_feed_task()`
  like the real one** (the csap-black FR-005 reproducer stubbed
  `_connect` as a no-op and therefore missed D-A — the stub must be
  faithful to the task-spawning behavior, not just the I/O), then calls
  `stop()` and asserts zero pending session tasks; plus a reconnect-path
  variant. Fails against current code.
- GREEN: the fixes; assert `stop()` leaves no session-spawned task
  pending and exactly one feeder existed at any time (fixes the lost
  first utterance as a side effect — witness with the existing
  first-listen tests if present).

## Acceptance

- A-1: RED witnesses committed first, failing against 0.1.12 behavior.
- A-2: Post-fix, one feeder per session at all times; `stop()` awaits
  all spawned tasks; no pending-task/unraisable warnings at loop close
  in the unit witnesses (initial-start, reconnect, and stop paths).
- A-3: azure_stt verified against the same contract (fixed or shown
  clean).
- A-4: Version bump + release; downstream csap-black known-bug doc
  updated to FIXED with the version, and the harness `No crash`
  criterion expected green on happy-path HP-35 runs against the new
  release (field witness, downstream repo).

## Out of scope

- Harness/csap changes (the callsite teardown owner already landed:
  csap-black FR-003).
- ElevenLabs SDK internals (message-handler/keepalive tasks are owned by
  the SDK connection object; closing the connection properly is the
  boundary — do not reach into SDK task internals).

## Evidence

- Source (0.1.12 == installed == latest): `voice_runtime/providers/`
  `elevenlabs_stt.py:59-72` (start double-create), `:123-135`
  (`_ensure_feed_task`), `:137-144` (stop cancel-without-await).
- csap-black `docs/known-bug-voice-runtime-duplicate-feed-task.md`
  (symptom history since 2026-06-18 / 0.1.8).
- csap-black `test-cases/_shared/teardown_repro.py` +
  `feature-requests/FR-005-teardown-crash-investigation.md` (mechanism
  reproducer; note its `_connect` stub gap described above).
- csap `docs/case-studies/2026-08-20-hp35-CA19b1f8764f4ae3b674fb7a23ee53c90e/`
  (Finding 3: post-FR-003 run, stop() ran, duplicate still orphaned) and
  `docs/case-studies/2026-08-20-hp93-CAc8b7f44b6c02f2d6becb78d32f7377b9/`
  (residual: reconnect-spawned orphans).
