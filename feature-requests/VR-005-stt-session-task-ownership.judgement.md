# Judgement: VR-005 STT session must own every task it spawns

**Verdict:** APPROVED WITH REVISIONS — the lifecycle defect is real and the task-ownership direction is minimal, but authority activates only after the FR folds in the ownership-boundary, evidence, Azure, and downstream-scope revisions below.

**Reviewed against:** `projects/voice_runtime/feature-requests/VR-005-stt-session-task-ownership.md`; `projects/voice_runtime/voice_runtime/providers/elevenlabs_stt.py`; `projects/voice_runtime/voice_runtime/providers/azure_stt.py`; `projects/voice_runtime/feature-requests/NC-340-stt-feed-survives-reconnect.md`; `projects/voice_runtime/feature-requests/NC-340-stt-feed-survives-reconnect.judgement.md`; `projects/voice_runtime/tests/test_nc340_stt_feed_survives_reconnect.py`; `projects/voice_runtime/tests/test_azure_stt.py`; `projects/voice_runtime/tests/test_session.py`; `projects/voice_runtime/README.md`; `projects/voice_runtime/CHANGELOG.md`; `.github/skills/judge-fr/doctrine.md`; `.github/skills/judge-fr/judgement.template.md`; `ARCHITECTURE.md`; repo doctrine from the active project instructions. Cited downstream/csap evidence files named by the FR (`csap-black docs/known-bug-voice-runtime-duplicate-feed-task.md`, `csap-black test-cases/_shared/teardown_repro.py`, `csap-black feature-requests/FR-005-teardown-crash-investigation.md`, and the two `csap docs/case-studies/2026-08-20-*` directories) were not present under this repository and were not consumed.

## What is sound

The primary ElevenLabs defect is source-evidenced. `start()` awaits `_connect()` and then creates `stt_feed` again (`elevenlabs_stt.py:59-71`), while `_connect()` already calls `_ensure_feed_task()` after establishing the socket (`elevenlabs_stt.py:74-123`). On initial start, `_feed_task` is `None`, so `_ensure_feed_task()` creates task A (`elevenlabs_stt.py:123-135`) and `start()` immediately overwrites the only reference with task B (`elevenlabs_stt.py:69-71`). `stop()` then cancels only the referenced task and does not await it (`elevenlabs_stt.py:137-144`). That is exactly the kind of external-boundary lifecycle leak the repo doctrine names as a composition defect: correct parts joined by an unsafe policy.

The proposed cure is architecturally aligned. `README.md` defines STT providers as runtime-managed lifecycle objects with `start()` and `stop()` methods (`README.md:127-140`), and the transport starts/stops the provider rather than consumers reaching into provider internals (`README.md:148`). A provider-level invariant that every session-spawned async job is reachable and drained by `stop()` preserves that protocol instead of pushing teardown into callers.

The FR is one concern, not a bundle: initial duplicate feeder, reconnect future orphaning, and cancel-without-await are all manifestations of the same missing ownership contract. Strategic classification: **contrib/provider lifecycle defect fix**, not a YAMLGraph framework primitive. The authorized surface is the `voice_runtime` STT provider lifecycle and its focused tests.

## Required revisions

### R-1: Define the ownership boundary precisely

Revise the FR to distinguish **session-spawned asyncio tasks/futures** from **SDK-owned internal tasks**. The current text says reconnect replacement `_connect` tasks and SDK message-handler/keepalive tasks are not rooted (`VR-005:33-39`), but the same FR says ElevenLabs SDK internals are out of scope and must be handled only by closing the SDK connection (`VR-005:100-104`). Both cannot be the implementation target.

Required wording: the session must own and drain every task or future it directly creates or schedules, including `asyncio.create_task(...)` feed tasks (`elevenlabs_stt.py:69-71`, `:133-135`; `azure_stt.py:106-109`) and `asyncio.run_coroutine_threadsafe(...)` reconnect futures (`elevenlabs_stt.py:166`, `:350`; `azure_stt.py:169-173`). SDK-owned tasks are not individually tracked; the session satisfies that boundary by closing/stopping the current provider connection before `stop()` returns.

### R-2: Make reconnect-future ownership a mechanical deliverable

Add a concrete implementation rule for dropped `run_coroutine_threadsafe` results. ElevenLabs discards the `Future` returned by long-TTS `_connect()` and fatal-error `_reconnect_after_error()` scheduling (`elevenlabs_stt.py:166`, `:350`); Azure does the same in `_on_canceled()` (`azure_stt.py:169-173`). `stop()` cannot await or cancel work it never recorded.

Required: the FR must specify a task/future registry or equivalent named attributes for scheduled reconnect work, and `stop()` must cancel and wait for those handles before logging stopped. The acceptance criteria must assert not only "zero pending session tasks" but also "no owned reconnect future remains running after `stop()`."

### R-3: Tighten the RED witnesses around the actual double-create path

The proposed RED test direction is correct because it explicitly warns that a no-op `_connect` stub would miss D-A (`VR-005:73-79`). Make it mechanically stricter: the initial-start witness must execute a faithful `_connect()` path that calls `_ensure_feed_task()` before `start()` resumes, then prove current behavior creates two feeders and loses one handle.

Required: AC must name the failing assertion shape. For example, instrument `_feed_audio`/task creation so the RED test observes two session-created feed tasks after `start()` on current `0.1.12` behavior, then after the fix observes exactly one feed task for the session lifetime until an intentional reconnect/recreation path. A test that merely inspects final `_feed_task` identity is insufficient because the orphaned first task is the defect.

### R-4: Convert Azure from "verify" prose into a bounded criterion

The FR says to "Verify the same contract in `providers/azure_stt.py` — fix if the same pattern exists" (`VR-005:66-69`). That is too discretionary for enforcement. The source shows Azure does not have the initial double-create shape (`azure_stt.py:71-110`) and already awaits the feed task on normal stop (`azure_stt.py:112-125`), but it does schedule reconnect work from the SDK callback without retaining the returned future (`azure_stt.py:153-173`).

Required: replace "fixed or shown clean" with explicit Azure acceptance: no duplicate feeder expected; normal `stop()` continues to await the feed task; any Azure reconnect future scheduled by `_on_canceled()` is owned and drained/cancelled by `stop()`; if implementation proves Azure has no running reconnect future at stop under a faithful mocked callback, record that proof in the FR and test.

### R-5: Move downstream release and csap documentation out of the enforcement gate

A version bump and `voice_runtime` changelog/release note are within this repo's normal release surface, but csap-black known-bug documentation and HP-35 harness verification are downstream artifacts (`VR-005:93-96`). They are useful operational confirmation, not authority for the voice_runtime enforcer to edit external repositories.

Required: revise A-4 into two parts: an in-repo release/changelog criterion for `voice_runtime`, and a post-release observation to be recorded after a downstream consumer pins the release. Do not authorize csap/csap-black edits or harness runs as blocking implementation criteria under this FR.

### R-6: Attach or inline the unavailable field evidence

The source evidence is enough to establish D-A and D-C, but the field claims for HP-35/HP-93 and the historical known-bug document are not present in the input closure. The FR may cite external evidence, but a judge cannot treat absent files as reviewed.

Required: either add committed evidence excerpts under `projects/voice_runtime/feature-requests/evidence/` or revise the Evidence section to include exact repository/path/commit or issue URLs and mark them as human-auditable external references. Do not phrase unavailable downstream artifacts as evidence already reviewed by this judgement.

## Scope is frozen

| Deliverable | Surface |
|---|---|
| D-1 | `projects/voice_runtime/voice_runtime/providers/elevenlabs_stt.py` |
| D-2 | `projects/voice_runtime/voice_runtime/providers/azure_stt.py` only for the bounded ownership check/fix described in R-4 |
| D-3 | Focused unit tests under `projects/voice_runtime/tests/`, preferably `test_vr005_stt_session_task_ownership.py` or equivalent |
| D-4 | `projects/voice_runtime/feature-requests/VR-005-stt-session-task-ownership.md` revision/status/implementation notes |
| D-5 | `projects/voice_runtime/CHANGELOG.md`, `pyproject.toml`, and release metadata only if this FR performs the voice_runtime release |

Not authorized: consumer repository edits, csap/csap-black documentation updates, harness implementation changes, live ElevenLabs/Azure/Twilio tests as RED witnesses, reaching into SDK private task internals, new public STT provider APIs, broad provider rewrites, changes to transport/session ownership semantics outside STT stop/start/reconnect lifecycle, YAMLGraph graph/prompt changes, or judge/review/enforcement doctrine changes.

## Revised acceptance criteria

- [ ] AC-01: ElevenLabs initial `start()` creates exactly one session-owned feed task. The RED witness faithfully executes a `_connect()` path that calls `_ensure_feed_task()` and fails on current behavior by observing two created feed tasks / one orphaned handle.
- [ ] AC-02: ElevenLabs `stop()` cancels and awaits every session-owned feed task and scheduled reconnect future before logging/stating stopped; no session-owned task/future remains pending at loop close in the unit witness.
- [ ] AC-03: ElevenLabs long-TTS and fatal-error reconnect scheduling records the returned `run_coroutine_threadsafe` handle or equivalent owned future, and `stop()` drains/cancels it deterministically.
- [ ] AC-04: The one-feeder invariant holds across reconnect: at most one live feeder consumes the inbound queue at any time, and intentional feeder recreation only happens after the prior feeder is done/cancelled.
- [ ] AC-05: Azure is covered by the same ownership contract at its actual seam: no duplicate feed task on `start()`, normal stop still awaits the feeder, and any `_on_canceled()` scheduled reconnect future is owned and drained/cancelled by `stop()` or proven absent by a faithful mocked callback test.
- [ ] AC-06: SDK-owned message-handler/keepalive internals are not individually inspected or cancelled; tests assert the session closes/stops the current SDK connection object and drains only session-owned handles.
- [ ] AC-07: Existing NC-340 feeder-liveness guarantees still pass: long-TTS reconnect resumes feeding and reconnect-window send failures do not break the feeder.
- [ ] AC-08: `voice_runtime` changelog/release metadata records the task-ownership fix. Post-release only: after a downstream consumer pins the release, record HP-35/HP-93 or equivalent harness observations back into the FR without authorizing external repo edits here.

## Conditions for enforcement

| # | Condition | Severity |
|---|---|---|
| C-1 | Fold R-1 through R-6 into the FR before implementation authority is exercised. | GATE |
| C-2 | Add RED tests before production code; they must fail against the current ElevenLabs double-create and dropped-reconnect-handle behavior, not merely assert the final `_feed_task` value. | GATE |
| C-3 | Track and drain only session-created tasks/futures; do not reach into ElevenLabs or Azure SDK private task internals. | GATE |
| C-4 | Preserve the public `SttProvider` protocol (`start`, `stop`, callbacks, `set_speaking`) and keep the change internal to provider lifecycle management. | GATE |
| C-5 | Do not modify csap/csap-black or harness repositories under this FR; downstream verification is post-release evidence only. | GATE |

Authority granted: after the required FR revisions are folded in, implement an internal STT provider task-ownership contract so ElevenLabs and the bounded Azure reconnect seam retain, cancel, and await all session-spawned feed/reconnect handles before `stop()` returns.
