# Judgement: VR-015 Google STT speech-activity pulse

**Verdict:** APPROVED WITH REVISIONS — the provider-local fix is sound, but contradictory timing criteria, underspecified test wiring and live-check evidence must be corrected; implementation authority activates only after the revisions are folded into the FR and a human promotes this advisory DRAFT.

**Reviewed against:** Committed artifacts only, reviewed 2026-11-17. `VR/` below is `/Users/sami.j.p.heikkinen/src/yamlgraph/projects/voice_runtime/` at `9a3c68ad537735ce59f2ea7addf7f4226471c80d`; `CSAP/` is `/Users/sami.j.p.heikkinen/src/customer-service-agent-platform/` at `684b38721d5085b9948dfebf38e7eddb1455183c`. Both tracked worktrees were clean.

- FR: `VR/feature-requests/VR-015-google-stt-speech-activity-pulse.md` (entire file).
- Cited implementation evidence: `VR/voice_runtime/providers/google_stt.py` (entire file); `VR/voice_runtime/providers/azure_stt.py:350-379`; `VR/voice_runtime/providers/_google_config.py:105-149`; `VR/tests/test_vr013_google_stt.py:1-330`.
- Cited parent and release doctrine: `VR/feature-requests/VR-013-google-chirp-speech-providers.md` (header, problem, provider facts and proposed-solution excerpt); `VR/reference/release-checklist.md` (entire file).
- Cited consumer evidence: `CSAP/feature-requests/NC-586-chirp3-test-stt.md`; `CSAP/feature-requests/evidence/NC-586-chirp3-test.md`; `CSAP/feature-requests/evidence/2026-11-16-chirp3-speech-check.md`; `CSAP/actions/real/silence_detector_action.py`; `CSAP/services/stt.py`; the silence-threshold matches at `CSAP/config/voice_coordinator_navigator.yaml:458-461`.
- Judge doctrine and format: `CSAP/.github/skills/judge-fr/doctrine.md`; `CSAP/.github/skills/judge-fr/judgement.template.md`. Project doctrine: `CSAP/.github/copilot-instructions.md`, including its judgement, traceability and unit-test-boundary rules.

The FR's embedded B-1 event table is treated as the FR's evidence, not as an independently retrieved pod log. No chat narrative, working notes, cloud state or new runtime experiments were consumed. The instruction-referenced `CSAP/docs/context/voice-projects.md` and `VR/.github/copilot-instructions.md` were absent and supplied no evidence. This draft is intentionally written to the requested `CSAP/tmp/draft-judgement.md`, not promoted to the canonical sibling judgement.

## What is sound

Google's current BEGIN branch emits one empty callback and its END branch only records a timestamp (`VR/voice_runtime/providers/google_stt.py:405-419`). Azure's interim callback explicitly represents continuing speech (`VR/voice_runtime/providers/azure_stt.py:367-379`), while CSAP deliberately discards interim text and forwards the event (`CSAP/services/stt.py:63-79`). A bounded provider-local pulse addresses a real timing mismatch without changing the consumer API.

| Rubric criterion | Assessment and evidence |
|---|---|
| Scope | Minimal production surface: one provider, no public configuration or transport change (VR-015:43-56, 70-74, 100-106). Preserve that boundary. |
| Consistency | Needs R-1 and R-2: stopping at END conflicts with AC-04's through-final guarantee; preserving both immediate BEGIN callbacks conflicts with AC-06's unqualified rate bound (VR-015:46-54, 89-94). |
| Measurability | Most offline assertions are concrete. AC-04 and AC-06 need precise event windows; AC-10's baseline and transcript comparator need R-4 (VR-015:88-98). |
| Feasibility | Existing `_schedule_owned`, `_owned_futures`, time seams and stream teardown support this change without new dependencies (`google_stt.py:31-32, 134-145, 172-185, 331-339`). R-3 must make the proposed separate test module collectable. |
| Architecture alignment | Normalize the timing difference in the provider, reusing task ownership rather than adding FSM or session policy (VR-015:46-58). The parent explicitly warned that callback cadence differed (`VR-013:20-22`); this FR must document its bounded new cadence, not claim full Azure equivalence. |
| Single responsibility | One concern: Google's continuing-speech signal. Tests, bounded live evidence and patch documentation support that concern; no split is needed (VR-015:41-74). |
| Strategic classification | **Contrib/example**, applied here as an existing-provider bug fix with one demonstrated consumer. Existing callback and task abstractions suffice; no framework primitive is justified (VR-015:58, 76-82). |
| Testability | Virtual time and real SDK response builders are suitable (`test_vr013_google_stt.py:63-306`). R-1 through R-3 are required before the criteria yield unambiguous behavioural RED tests rather than fixture or scheduling failures. |

## Required revisions

### R-1: Bound the guarantee to active speech and correct the reproduction

VR-015:49-50 stops pulses at END or a final, but AC-04 at line 91 demands gaps of at most 0.25 s all the way to the final. The FR itself reports END-to-final delays of 420-924 ms (line 33); the parent reports 450-600 ms (`VR-013:34`). No END-stopping implementation can promise the existing AC-04 over those delays.

Replace AC-04 with a timestamped synthetic reproduction: commit `"Kyllä."`, BEGIN at relative `t=0`, END at `t=1.6`, final `"juuri..."` at `t=2.1`. Assert the immediate callback and periodic callbacks at `0.25, 0.50, ..., 1.50`, no periodic callback after END, and the final text committed exactly once at `2.1`. The post-END gap is intentionally allowed. Label this a synthetic analogue, not an exact replay of the B-1 timestamps, whose BEGIN-to-final interval is 1.617 s and whose specific END time is not given.

Revise the ideal result, changelog wording, blast-radius claim and K-3 (lines 37, 67, 80, 114) to say that the change fills event gaps during reported active speech only. CSAP does **not** wait unconditionally for a final: it emits completion once its elapsed threshold is met (`silence_detector_action.py:67-97`). A last-pulse-to-END remainder of up to 0.25 s plus a 0.924 s final delay can exceed its 0.8 s threshold. Also document that genuine speech continuing beyond the 30 s cap loses the synthetic still-speaking signal. Do not extend pulses past END or change CSAP timers to conceal these limits.

### R-2: Separate immediate BEGIN notifications from periodic-task cadence

VR-015:46 preserves the immediate callback on every BEGIN. With BEGINs at `t=0` and `t=0.1`, that necessarily produces two callbacks within 0.25 s, contradicting AC-06's parenthetical at line 93.

Rewrite AC-06 to assert immediate callbacks at `0` and `0.1`, cancellation of the old periodic schedule, and restarted periodic callbacks at `0.35, 0.60, 0.85`. Assert no old-schedule callback at `0.25, 0.50, 0.75`, and at most one live periodic producer after cancellation has settled. The periodic rate bound excludes immediate BEGIN callbacks and real interim results. Keep the existing immediate BEGIN behaviour.

For AC-01, explicitly drain ready callbacks at the `2.0` virtual-time boundary before asserting nine notifications; do not make test success depend on whether the assertion coroutine or the pulse coroutine runs first at that instant.

### R-3: Specify runnable harness reuse and complete the promised lifecycle witnesses

The new test-file plan lists classes and response builders but not the fixtures and setup needed to run them (VR-015:86). The existing `vt`, `sdk` and autouse environment-cleanup fixtures are local to `test_vr013_google_stt.py:217-239`, not automatically available in another test module.

Amend the test plan to explicitly import/register the existing fixtures and required helpers in the new module, including environment cleanup, `start`, `stop` and the optional-Google import skip, without copying the harness or changing unrelated tests. Give the new tests their own `pytest.mark.req("VR-015")`; do not inherit the old module's VR-013 requirement marker. Require successful collection with the Google extra installed before recording RED.

Expand AC-08's witnesses to cover normal input EOF and terminal stream failure as well as retry, forced rotation and explicit stop. After each stream ends, assert that the old pulse produces no callbacks during backoff, closure or the replacement stream before a new BEGIN; after a new BEGIN, exactly one periodic sequence may resume. Assert owned tasks are finished after stop.

Add focused witnesses for the already specified behaviour at VR-015:55-56: pulses are allowed during the post-speaking echo window, and a real interim callback is forwarded unchanged without cancelling or duplicating the periodic producer. These are existing scope, not new public behaviour.

### R-4: Correct the live baseline and make the live gate reproducible and bounded

The paragraph baseline is **seven**, not one, recognizing events: both Google paragraph rows show `recognizing=7`, WER `0.156` (`CSAP/feature-requests/evidence/2026-11-16-chirp3-speech-check.md:95-105`). The value one belongs to the short-sentence runs. Correct VR-015:20 and :97 accordingly.

Replace the placeholder in AC-10 with the cited Run 3/4 synthetic paragraph and identify the comparator as the **Azure TTS -> Google STT** row at evidence line 105. Require command exit 0, `recognizing >= 20`, numeric WER `<= 0.156`, and the complete transcript recorded for inspection. Remove the undefined separate phrase "transcript ... no worse"; neither one aggregate count nor WER proves clinical accuracy or telephone turn-taking.

Set `GOOGLE_SPEECH_PROJECT=scp-tenant-csap-test` explicitly for the check, with the same `eu`, `chirp_3`, `fi-FI` and `supershort` settings, and record the effective non-secret settings, package/source version, exact command and full output in `VR/feature-requests/evidence/VR-015-google-stt-speech-activity-pulse.md`. Bound the initial check to one invocation with timeout 45 s, no automatic retries, synthetic text only and no configuration/deployment changes. A failed gate is recorded as failed, not retried until a passing sample appears.

Record explicit owner answers before enforcement: **Does the owner accept that active speech beyond the 30 s cap, post-END final latency and stream replacement remain outside the pulse guarantee? Does the owner authorize this one Azure TTS/Google STT desk-check invocation and its spend?** Existing CSAP telephone-trial permission is not an unlimited library-test budget. A negative answer returns the relevant policy to FR amendment; the enforcer must not choose a different cap, timer, spend limit or rollout policy.

## Scope is frozen

| Deliverable | Surface |
|---|---|
| D-1 | `VR/voice_runtime/providers/google_stt.py`: private bounded pulse, single periodic producer, existing callback suppression and owned-task cleanup. |
| D-2 | `VR/tests/test_vr015_google_stt_pulse.py`: virtual-time behavioural tests with explicit reuse of existing harness fixtures/helpers. |
| D-3 | `VR/README.md`, `VR/CHANGELOG.md`: precise interval, termination conditions, cap and remaining limitations. |
| D-4 | `VR/pyproject.toml`: next patch-version bump from the actual enforcement baseline, under the existing release checklist. |
| D-5 | `VR/feature-requests/VR-015-google-stt-speech-activity-pulse.md` and `VR/feature-requests/evidence/VR-015-google-stt-speech-activity-pulse.md`: folded revisions, owner answers, implementation status and check results. |
| D-6 | Canonical sibling judgement after human promotion; this requested temporary draft does not replace it. |

Not authorized: CSAP code, dependency pins, deployment or NC-586 Phase B calls; other providers, factories, transports, sessions or `stt_tee`; endpointing/configuration changes; new callbacks, settings, dependencies, shared timing frameworks, metrics or CI/hooks/doctrine changes; investigation of Google's missing interim text; extending pulses beyond END; automatic release publication or consumer upgrades. Consumer turn-taking qualification remains a separate CSAP gate.

## Revised acceptance criteria

- [ ] AC-01: With Google extras installed, the new test module collects successfully using the existing harness and VR-015 requirement marking. Tests on the unchanged provider demonstrate missing periodic callbacks through assertion failures, not missing imports, fixtures or skipped Google coverage. Record RED before GREEN.
- [ ] AC-02: BEGIN emits one immediate empty notification, followed by notifications every 0.25 s; after draining callbacks due through virtual `t=2.0`, exactly nine notifications have occurred.
- [ ] AC-03: END stops periodic notifications, with the count stable for the following 2 s. A final without END also stops them, including committed, empty, speaking-suppressed and echo-discarded finals; existing commit/discard behaviour remains intact.
- [ ] AC-04: The R-1 synthetic continuation emits pulses through `t=1.50`, stops them at END `t=1.6`, and commits the exact second final once at `t=2.1`. No maximum-gap assertion spans END-to-final latency.
- [ ] AC-05: With no END/final, the pulse terminates at the 30 s deadline, emits no notification at or after that deadline and logs exactly one `WARNING` with the specified cap message. Advancing another 2 s produces neither callbacks nor another cap warning.
- [ ] AC-06: The two-BEGIN schedule in R-2 preserves both immediate callbacks and only the restarted periodic sequence. Advancing beyond cancellation verifies that the old periodic schedule cannot emit.
- [ ] AC-07: Speaking suppresses callbacks without creating another producer. Clearing speaking allows the next scheduled pulse, including inside the echo window. Real interim text is forwarded unchanged and the periodic sequence continues.
- [ ] AC-08: Explicit stop, normal input EOF, terminal failure, retry and forced rotation terminate the old pulse. No old callbacks occur after stream teardown or in a replacement stream before its BEGIN. Stop leaves no unfinished owned task; a new stream's BEGIN can start exactly one new sequence.
- [ ] AC-09: The full existing suite passes unchanged, including the original Google tests and Azure/ElevenLabs/mock tests. Google coverage runs with the extra installed. `ruff check voice_runtime/`, a clean wheel build and the existing PR checks pass per `reference/release-checklist.md:79-92`; evidence records commands and outcomes.
- [ ] AC-10: After explicit owner approval, the single bounded desk-check invocation in R-4 exits 0, reports `recognizing >= 20` and WER `<= 0.156` for the cited Azure-voiced paragraph. Version/settings, command, full transcript and output are recorded at the named evidence path. Failure blocks completion; synthetic desk-check success does not certify CSAP calls or clinical accuracy.
- [ ] AC-11: README and changelog accurately describe the bounded timing change and limitations; the next patch version is derived from the actual release baseline. The FR records folded revisions, owner answers, implementation status, decisions and evidence. Documentation/version checks are one-time evidence, not document-wording or deployment unit tests.

## Conditions for enforcement

| # | Condition | Severity |
|---|---|---|
| C-1 | Fold R-1 through R-4 into the committed FR and have a human review/promote this DRAFT before it grants implementation authority. | GATE |
| C-2 | Record affirmative owner answers to both R-4 questions before implementation/live execution; do not infer acceptance of the 30 s limitation or authorize spend on the owner's behalf. | GATE |
| C-3 | Honour the frozen provider-only surface. Any need to change event semantics beyond this bounded pulse, public interfaces, other providers, CSAP policy or enforcement infrastructure requires amendment and judgement, not opportunistic repair. | GATE |
| C-4 | Demonstrate behavioural RED and GREEN with runnable Google coverage; satisfy the corrected cadence, cap and teardown criteria without changing original provider tests to accommodate regressions. | GATE |
| C-5 | Complete the named checks and record a failing live gate honestly. No patient/caller recordings, automatic retries, new credentials/IAM, region changes, deployment or telephone calls are authorized. | GATE |
| C-6 | Consumer dependency rollout and telephone acceptance remain under a separate CSAP authority. No claim that this patch alone proves callers are never cut off, or that clinical recognition is safe, is authorized. | GATE |

Authority granted: **None by this advisory DRAFT.** After human promotion, folded revisions and the applicable gates, authority is limited to the bounded Google-provider pulse and its listed tests, documentation, patch metadata and evidence.
