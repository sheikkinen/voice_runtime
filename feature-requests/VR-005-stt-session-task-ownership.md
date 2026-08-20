# Feature Request: VR-005 STT session must own every task it spawns (duplicate feed task + reconnect orphans)

**Priority:** HIGH (sole remaining cause of harness `No crash` failures on happy-path runs; also loses the first caller utterance)
**Type:** Bug fix
**Status:** ENFORCED + RELEASED 0.1.13 (2026-08-20; RED dd760a6, GREEN 051d03c, release 004ffcf/v0.1.13, PyPI live; judged Approved with revisions, R-1–R-6 folded in). Open: AC-08 post-release field observation after downstream pins.
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

Task-ownership contract for the session: the session must own and drain
every task or future **it directly creates or schedules** — feed tasks
via `asyncio.create_task(...)` and reconnect work via
`asyncio.run_coroutine_threadsafe(...)`. SDK-owned internal tasks
(message handler, keepalive) are NOT individually tracked; the session
satisfies that boundary by closing/stopping the current provider
connection before `stop()` returns (R-1).

1. Fix D-A: `start()` must not double-create — either `start()` relies
   on `_connect()`'s `_ensure_feed_task()` (delete line 69's create), or
   `_connect()` must not pre-create on initial start. One feeder per
   session, ever.
2. Fix D-B (R-2, mechanical): retain every `run_coroutine_threadsafe`
   handle the session schedules (long-TTS `_connect()`, fatal-error
   `_reconnect_after_error()`) in a session-owned future registry or
   equivalent named attributes; `stop()` cancels and waits for those
   handles before returning. No dropped futures.
3. Fix D-C: `stop()` awaits cancelled tasks/futures with
   `gather(*tasks, return_exceptions=True)` before returning, so
   "PersistentSttSession stopped" in the log MEANS all session-owned
   work is done (signal = state, not intent — the csap NC-445/446
   lesson).
4. Azure (R-4, bounded): source review shows `azure_stt.py` has NO
   initial double-create and its normal `stop()` already awaits the
   feed task. The one gap is `_on_canceled()` scheduling
   `_reconnect_after_error()` via `run_coroutine_threadsafe` without
   retaining the future (azure_stt.py:169-173). Required: that
   reconnect future is owned and drained/cancelled by `stop()`, or
   proven absent at stop under a faithful mocked callback test. No
   duplicate-feeder change expected on the Azure side.

## Red/green witness

- RED first (R-3): the initial-start witness must execute a faithful
  `_connect()` path that calls `_ensure_feed_task()` before `start()`
  resumes (the csap-black FR-005 reproducer stubbed `_connect` as a
  no-op and therefore missed D-A — the stub must be faithful to the
  task-spawning behavior, not just the I/O; the NC-340 test's
  `_patch_elevenlabs` pattern runs the REAL `_connect` offline and is
  the preferred harness). The failing assertion shape: after `start()`
  on 0.1.12 behavior, the test observes TWO session-created feed tasks
  (one orphaned handle); merely inspecting the final `_feed_task`
  identity is insufficient because the orphaned first task IS the
  defect. Plus a reconnect-path variant asserting the scheduled
  reconnect handle is owned, and a stop-awaits variant.
- GREEN: the fixes; assert `stop()` leaves no session-spawned
  task/future pending and exactly one feed task exists for the session
  lifetime until an intentional reconnect/recreation path (fixes the
  lost first utterance as a side effect).

## Acceptance (revised per judgement AC-01..AC-08)

- AC-01: ElevenLabs initial `start()` creates exactly one session-owned
  feed task. The RED witness faithfully executes a `_connect()` path
  that calls `_ensure_feed_task()` and fails on current behavior by
  observing two created feed tasks / one orphaned handle.
- AC-02: ElevenLabs `stop()` cancels and awaits every session-owned
  feed task and scheduled reconnect future before logging stopped; no
  session-owned task/future remains pending at loop close in the unit
  witness.
- AC-03: ElevenLabs long-TTS and fatal-error reconnect scheduling
  records the returned `run_coroutine_threadsafe` handle, and `stop()`
  drains/cancels it deterministically.
- AC-04: The one-feeder invariant holds across reconnect: at most one
  live feeder consumes the inbound queue at any time; intentional
  feeder recreation only happens after the prior feeder is
  done/cancelled.
- AC-05: Azure covered at its actual seam: no duplicate feed task on
  `start()`, normal stop still awaits the feeder, and any
  `_on_canceled()` scheduled reconnect future is owned and
  drained/cancelled by `stop()` or proven absent by a faithful mocked
  callback test.
- AC-06: SDK-owned message-handler/keepalive internals are not
  individually inspected or cancelled; tests assert the session
  closes/stops the current SDK connection object and drains only
  session-owned handles.
- AC-07: Existing NC-340 feeder-liveness guarantees still pass:
  long-TTS reconnect resumes feeding; reconnect-window send failures do
  not break the feeder.
- AC-08 (R-5, split): in-repo — `voice_runtime` changelog/release
  metadata records the task-ownership fix. Post-release only — after a
  downstream consumer pins the release, record HP-35/HP-93 or
  equivalent harness observations back into this FR; NO csap/csap-black
  edits or harness runs are authorized as blocking criteria under this
  FR.

## Out of scope

- Harness/csap changes (the callsite teardown owner already landed:
  csap-black FR-003).
- ElevenLabs SDK internals (message-handler/keepalive tasks are owned by
  the SDK connection object; closing the connection properly is the
  boundary — do not reach into SDK task internals).

## Evidence

In-repo, judge-reviewed:

- Source (0.1.12 == installed == latest): `voice_runtime/providers/`
  `elevenlabs_stt.py:59-72` (start double-create), `:123-135`
  (`_ensure_feed_task`), `:137-144` (stop cancel-without-await),
  `:166` and `:350` (dropped reconnect futures);
  `azure_stt.py:169-173` (dropped `_on_canceled` reconnect future).

External references (R-6: human-auditable, NOT reviewed by the
judgement — they live in sibling repos outside this repository's input
closure):

- csap-black-box-tests repo (~/src/csap-black-box-tests):
  `docs/known-bug-voice-runtime-duplicate-feed-task.md` (symptom
  history since 2026-06-18 / 0.1.8);
  `test-cases/_shared/teardown_repro.py` +
  `feature-requests/FR-005-teardown-crash-investigation.md` (mechanism
  reproducer; note its `_connect` no-op stub gap described above).
- csap repo:
  `docs/case-studies/2026-08-20-hp35-CA19b1f8764f4ae3b674fb7a23ee53c90e/`
  (Finding 3: post-FR-003 run, stop() ran, duplicate still orphaned);
  `docs/case-studies/2026-08-20-hp93-CAc8b7f44b6c02f2d6becb78d32f7377b9/`
  (residual: reconnect-spawned orphans).

## Enforcement record (2026-08-20)

- RED dd760a6: 5 witnesses in `tests/test_vr005_stt_session_task_ownership.py`,
  all failing on 0.1.12 for the condemned behavior (2 feeders observed;
  `_feed_task` still cancelling after `stop()`; pending `_connect` /
  `_reconnect_after_error` tasks in both providers).
- GREEN: `start()` no longer double-creates (one feeder via `_connect()`'s
  `_ensure_feed_task()`, now `_stopping`-guarded against post-stop
  resurrection); `_schedule_owned()` retains every `run_coroutine_threadsafe`
  handle in `_owned_futures` (done-callback discards) in BOTH providers;
  `stop()` cancels then `gather(..., return_exceptions=True)`s all owned
  work before closing the SDK connection. SDK internals untouched (AC-06):
  the boundary remains `close()`/`stop_continuous_recognition`.
- Full suite: 323 passed, 1 skipped (azure extra installed locally so the
  AC-05 witness runs; CI installs `[dev,elevenlabs]` and skips it — the
  existing repo pattern for azure-marked tests).
- Deviation from FR option space: took the "delete start()'s create" arm
  of D-A fix; added `_stopping` guard to `_ensure_feed_task` (AC-04's
  "recreation only after prior feeder done" + no post-stop resurrection).
- AC-08 in-repo: CHANGELOG Unreleased entry added. Post-release half open:
  release + downstream pin + HP-35/HP-93 field observation.
