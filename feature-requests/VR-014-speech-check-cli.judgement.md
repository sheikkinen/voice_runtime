# Judgement: VR-014 Cross-provider speech check CLI

**Verdict:** APPROVED WITH REVISIONS - the standalone diagnostic is justified, but RED provenance, spawn-safe testing, timeout cleanup and observable CLI semantics need correction; authority activates only after the revisions are committed and this DRAFT is human-reviewed.

**Reviewed against:** Committed artifacts only. `VR/` means `/Users/sami.j.p.heikkinen/src/yamlgraph/projects/voice_runtime` at `4d24a505633d3edc944270c996b86b55431381a3`; `CSAP/` means `/Users/sami.j.p.heikkinen/src/customer-service-agent-platform` at `a60fb2b9bb42682fbcfe8e8dd5f7d26ebb8739d2`.

- `VR/feature-requests/VR-014-speech-check-cli.md` (all; line references below abbreviate this as `FR`).
- Prior scope/authority: `VR/feature-requests/VR-012-google-chirp-stt-tts-providers.md:1-50`; its complete `.judgement.md`; `VR/feature-requests/VR-013-google-chirp-speech-providers.md:1-120`.
- Cited implementation evidence: `VR/voice_runtime/stt.py`; `tts.py`; `mock/stt.py`; `mock/tts.py`; `session.py:1-260,305-363`; `audio.py:1-220`; `providers/azure_stt.py:1-280`; `providers/azure_tts.py:1-240`; `providers/elevenlabs_stt.py:1-390`; `VR/scripts/probe_vr009_lid.py`; `VR/reference/release-checklist.md`.
- Governing contract: `CSAP/.github/skills/judge-fr/doctrine.md`; `CSAP/.github/skills/judge-fr/judgement.template.md`; `CSAP/.github/copilot-instructions.md:1-200`.
- Enclosing-project doctrine: `/Users/sami.j.p.heikkinen/src/yamlgraph/.github/copilot-instructions.md` (displayed excerpt through line 175), at `839e2819051cdd6268343e719d1d8977aa3a138b`.

The target repository was clean; consumed CSAP and enclosing-project doctrine files had no differences from HEAD. No author transcript, working notes, credentials, live service or external web content was used. This temporary advisory artifact does not promote or replace a canonical judgement. No judge was launched.

## Human promotion record — 2026-11-16

Round 1 was rendered by the YAMLGraph judge adapter against FR commit `4d24a50`, through the CSAP route (`scripts/judge.sh`, backend copilot, model `gpt-6.1-sol`); the yamlgraph adapter's pin `gpt-5.6-sol` is not available. The text above and below this section is the draft verbatim. The owner (Sami Heikkinen) reviewed the APPROVED WITH REVISIONS verdict on 2026-11-16 and accepted it ("fold").

Disposition (C-1, C-2): R-1 to R-6 and the revised acceptance criteria are folded into [VR-014](VR-014-speech-check-cli.md). Implementation authority covers only the frozen offline scope. The owner gates Q1 (Azure and ElevenLabs live spend) remain open and block AC-10.

## What is sound

The split is respected: this is one operational diagnostic using existing providers, not a Google backend, failover system or new orchestration framework (`FR:4,8,115-119,189-197`). The existing loop-less session really does permit capture through `set_mixer`/`tap_agent`, without a transport or mark responder (`session.py:136-140,194-195,311-328`; `azure_tts.py:97-101`). Precise audio formats, a bounded process per step, explicit partial-SKIP semantics and separate owner-approved live acceptance are useful constraints.

The strongest case against proceeding is that a manual probe can accumulate disproportionate machinery: process supervision, WAV parsing, scoring and two output formats. Nevertheless, release checks and incident diagnosis are two named uses, and the wheel-installed execution requirement is not met by the existing Azure-only script (`FR:11,17-25`; `scripts/probe_vr009_lid.py:1-13`). Keep this a single contrib diagnostic utility, not a framework primitive.

| Rubric criterion | Assessment and evidence |
|---|---|
| 1. Scope | Clear, independently shippable CLI concern; no further split required. Preserve the excluded provider/CI/failover surfaces (`FR:115-119,189-197`). |
| 2. Consistency | Hard termination cannot guarantee execution of STT `finally`; STT's default reference and optional-reference wording disagree (`FR:36,41,62,72,147`). R-3 and R-4 resolve these contracts. |
| 3. Measurability | Many examples are directly assertable, but row cardinality, output WAV encoding and several failure/metric witnesses are absent (`FR:39,67-79,103,134-159`). R-4 and R-6 close these gaps. |
| 4. Feasibility | Process isolation can bound blocked SDK calls, but parent factory patches do not survive spawn; Google dispatch is absent in both current factories (`FR:62,127,151`; `stt.py:10-27`; `tts.py:6-25`). R-2 and R-5 are prerequisites. |
| 5. Architecture alignment | Capture and existing factories fit; existing mocks must not be changed into audio synthesizers merely to make the harness work (`FR:117-118,145`; `mock/tts.py:22-50`). R-2 preserves that boundary. |
| 6. Single responsibility | Synthesis, recognition, comparison and reporting serve the same manual diagnostic, unlike the already separated Google implementation (`FR:19,31-43`; parent judgement R-1). |
| 7. Strategic classification | **Contrib/diagnostic utility**, not framework primitive: two named operational uses, existing public provider abstractions suffice (`FR:11,17-25,64-79`). No generalized runner/plugin API is justified. |
| 8. Testability | Most tests are derivable, but AC-01 explicitly reverses the parent's behavioral-RED requirement, while spawn and unmodified `MockTts` invalidate parts of the proposed fixtures (`FR:123-127,145`; parent judgement R-2). R-1 and R-2 repair the test seam rather than accepting setup failures as behavior. |

## Required revisions

### R-1: Restore the inherited behavioral-RED requirement

Replace AC-01's missing-module exception and its justification (`FR:123-125`). The parent judgement's R-2 explicitly rejects missing implementation imports as RED evidence, including imports caught inside a test; this successor cannot silently reverse that frozen constraint.

Allow a minimal importable CLI/worker skeleton to establish the new seam before the test-only RED commit. Record that scaffolding separately. The RED tests must collect and reach assertions about exit codes, rows, capture, scoring or deadlines; importing the skeleton or calling its inert entry point must not itself raise a setup exception. Keep the existing factories unchanged. Record the scaffold, RED and GREEN commit identities, and exclude fixture/import/pickling failures from valid RED evidence.

### R-2: Make the offline harness work under spawn

Replace the assumption that parent-process factory patching supplies child providers (`FR:62,127`). Specify an importable, test-only child entry point that installs its fake factories inside the spawned child and then calls the real step worker. A private callable seam in the supervisor is sufficient; do not add a CLI injection option, environment-selected plugin mechanism or public runner abstraction.

Use an audio-emitting fake whose `speak` calls the real session's `tap_agent` for the successful capture witness. Unmodified `MockTts` records text but never emits audio (`mock/tts.py:22-50`); use it only as a no-audio failure witness, or wire its existing callback explicitly in the test. Do not edit the mock provider.

Exercise the real spawn supervisor in timeout and result-transfer tests, not just worker functions in the pytest process. Fakes must expose their PID and observable calls through child-safe communication. All offline paths, including preflight, must avoid real credentials and requests. Add a successful capture/result-transfer case large enough to exceed a small IPC pipe buffer, so waiting for child exit before draining its audio cannot masquerade as an SDK timeout.

### R-3: Reconcile cleanup guarantees with hard deadlines

Replace the contradictory unconditional timeout-stop assertion (`FR:62,72,147`). A terminated or killed process does not execute Python `finally`.

Define this bounded sequence: at the step deadline, mark FAIL timeout and request cooperative cancellation; allow at most one second for cooperative teardown, then terminate; allow at most one further second before kill; reap the child within the existing deadline-plus-three-second bound. Normal exits and cooperative cancellation after successful `start` must enter `finally` and await `stop`. Hard termination is the explicit exception to that guarantee, not evidence that `stop` ran. A blocked or failed `stop` remains FAIL.

Apply the supervised deadline to imports, factory resolution and preflight, including ADC discovery, as well as construction, start, feeding, settling and stop. No unbounded credential lookup may occur in the parent before its timer. Do not claim process death retracts requests already accepted by a remote service; it prevents further execution by that local worker.

Make AC-06's elapsed bound a **single selected step** test. Multiple sequential steps each have their own budget; the entire multi-provider roundtrip command cannot be bounded by one timeout. Add witnesses for cooperative timeout after successful start, blocked synchronous work after successful start, never-returning start, blocked stop, and termination escalation. Assert FAIL, bounded exit and reaped child; assert `stop` only on paths capable of cooperative cleanup. If a timeout occurs, it must not prevent the next selected provider from being attempted.

### R-4: Specify optional-reference scoring and result rows

Resolve the conflict between a universal default text and "stt-with-text" (`FR:36,41,49,75-79`). In STT-only mode, omitted `--text` means no reference and no WER check; an explicitly supplied reference must normalize nonempty, and its WER uses the same strict-greater-than failure rule as roundtrip. Explicit `--max-wer` without an applicable reference is a usage error. TTS and roundtrip retain the fixed synthetic default. Name that exact sentence in the FR.

Define normalization mechanically, including Finnish letters: lowercase, delete Unicode punctuation characters, split/collapse whitespace; join committed segments in arrival order with a single space before scoring. Do not expand into number normalization.

Freeze row cardinality: TTS-only and STT-only emit one result per selected provider; roundtrip emits TTS then STT rows for each synthesizer/recognizer pair, with WER on the STT row and both identities retained. Failed or preflight-skipped synthesis must not trigger STT; emit its dependent STT row as SKIP with a dependency reason. Distinguish this dependency SKIP from missing configuration and retain exit 1 if either executed step failed.

Specify that `all` expands to the three named providers in listed order and repeated selections are deduplicated. Replace the stale reference to "AC-07 (live acceptance)" at `FR:113` with AC-09.

### R-5: Define unavailable-provider and preflight error classification

At this committed baseline, `google` is a recognized planned CLI name but is not accepted by either provider factory (`stt.py:10-27`; `tts.py:6-25`). The current spec promises independence from VR-013 yet permits SKIP only for missing extras/configuration (`FR:8,34,83-92`). Explicitly classify a provider implementation absent from the installed voice_runtime version as SKIP with that reason; do not modify factories or ship a Google stub here. Test `all` before Google exists and Google dispatch through a fake available implementation.

Limit missing-extra SKIP to a positively identified absent optional dependency for the selected mode. An arbitrary `ImportError`, syntax error, invalid provider configuration or unexpected constructor/preflight exception is FAIL, not "extra not installed". Credential discovery is preflight, not a speech validation request; only the declared missing-ADC exception is SKIP, with other discovery failures reported as FAIL. Preserve permission/authentication/quota failures as FAIL.

Check only capabilities required by the mode; roundtrip checks both providers before any synthesis request. Add a witness proving no synthesis request occurs when its recognizer cannot run. Keep reasons value-free and retain the existing planted-secret assertion.

### R-6: Add witnesses for the diagnostic's advertised outputs

Extend the focused tests, not the production scope (`FR:39,67-79,94-107,134-159`):

- Specify output WAV as format-tag-7, 8 kHz mono mu-law containing the captured bytes; `.ulaw` contains those same raw bytes. Verify both with synthetic data. Report an output-write error as FAIL, not a traceback followed by success.
- Assert TTS FAIL independently for empty capture, an error result and `on_error`, even if bytes were captured. Assert STT FAIL for no commits and callback-reported errors, even if another commit succeeded.
- Assert scoring through result status and exit code, not just the distance helper: WER equal to the threshold passes, above it fails, and STT without a reference has no WER. Cover insertion as well as the existing substitution/deletion fixtures.
- With controlled worker clocks/callbacks, assert first-byte timing, total duration, byte count/audio seconds and commit aggregation. Define end-of-input as the instant the last original audio frame is fed, before appended silence; measure lag to the last commit against that timestamp. Initialize the settling quiet window when feeding completes, and reset it on each later commit.
- Publish byte count, audio duration, recognizing count and result keys in JSON fields and table detail where there is no dedicated column. Use null/not-applicable for absent metrics, not invented zeroes. Assert metadata-first JSON and clean stdout while a fake emits provider logs.

These are synthetic-input unit tests. Keep actual wheel/version/build observations and live acceptance evidence as one-time FR records, not tests of real repository document wording or release state.

## Scope is frozen

| Deliverable | Surface |
|---|---|
| D-1 | `voice_runtime/speech_check.py`: parser, supported audio conversion, capture, existing-factory dispatch, bounded step supervisor, WER, table/JSON output and exits. Private helpers only. |
| D-2 | `tests/test_vr014_speech_check.py`; a test-only importable helper only if required for spawn. Synthetic fixtures, child-safe fakes and VR-014 markers. |
| D-3 | `README.md`, `CHANGELOG.md`, version field in `pyproject.toml`; next minor selected against the actual enforcement baseline. No runtime dependency additions. |
| D-4 | VR-014 implementation/evidence record; existing owner-approved Azure/ElevenLabs live matrix. Google rows remain conditional follow-up evidence, not this release's gate. |

Not authorized: Google implementation or factory changes; changes to providers, mocks, session, protocols or transports; failover/scheduling; generalized subprocess/plugin infrastructure; new audio formats; new runtime dependencies; console-script registration; CI/hooks/judge changes; consumer-repository edits or deployment; caller/patient recordings; credential access or billable probes without approval; tags or publication. Do not amend the FR or promote this draft while acting as this judge.

## Revised acceptance criteria

- [ ] AC-01: Minimal scaffolding, test-only behavioral RED and GREEN are separately identified as R-1 requires. Tests collect without setup/import/pickling failures, carry `@pytest.mark.req("VR-014")`, and make no live requests.
- [ ] AC-02: Mode/option validation covers the original invalid inputs plus explicit-versus-omitted STT reference, inapplicable `--max-wer`, non-finite numeric values, normalized-empty reference and deterministic deduplicated selection. Usage errors exit 2 with reasons.
- [ ] AC-03: Synthetic raw mu-law, tag-7 WAV and PCM16 WAV at 8 kHz mono are accepted; known samples use the existing encoder. Unsupported/truncated formats are rejected. Saved WAV/raw bytes and output-write failure behavior satisfy R-6.
- [ ] AC-04: Normalized word-level WER covers equality, substitutions, deletion, insertion, punctuation/case and empty hypothesis. Joined commits and threshold boundaries drive the correct STT/roundtrip status and exit.
- [ ] AC-05: Real loop-less session capture works with an emitting fake; the unmodified text-only mock fails for empty audio. Required TTS/STT error branches, cross-provider preflight, dependent rows, metrics and settling semantics have direct witnesses.
- [ ] AC-06: The actual spawn supervisor passes successful small/large result-transfer tests. Cooperative timeout, blocked TTS, never-returning STT start, blocked post-start work/stop and kill escalation satisfy R-3's per-step bound; no child survives, and later providers still run.
- [ ] AC-07: Declared missing configuration/dependency/implementation cases SKIP without secret values; unexpected preflight and attempted-request errors FAIL. Exit codes remain 0 for pass plus skip, 1 for any fail and 2 for all skip/usage errors. Google absence does not break the independent release.
- [ ] AC-08: Table/JSON rows and metadata satisfy R-4/R-6; every nonempty JSON stdout line is an object, meta is first, provider logs use stderr, and unrelated checkouts do not supply provenance. No-reference/absent metrics are explicitly not applicable.
- [ ] AC-09: Record fresh Python 3.11/3.12 full-suite runs with `.[dev,azure,elevenlabs]`, `ruff check voice_runtime/`, build and `twine check` results. A clean non-editable wheel outside the repo runs help and the missing-key Azure SKIP check with exit 2.
- [ ] AC-10: Only after recorded owner approval, run the original three Azure/ElevenLabs live commands with explicit Finnish ElevenLabs language and record all rows as PASS, output, versions, SHA and Azure region. Any intended SKIP/FAIL blocks release. Google follow-up awaits its implemented backend and applicable spend approval.
- [ ] AC-11: README documents usage, example output, scoring/reference defaults, all SKIP categories, exits, cooperative/hard timeout limits and recording privacy. Changelog/version use the actual next minor. FR records commits, decisions, evidence, approval and deviations.

## Conditions for enforcement

| # | Condition | Severity |
|---|---|---|
| C-1 | Human-review this DRAFT; it grants no implementation authority on its own. | GATE |
| C-2 | Fold R-1 through R-6 into the committed FR before implementation. Preserve the parent's behavioral-RED constraint and the explicit hard-kill cleanup limitation. | GATE |
| C-3 | Demonstrate a working spawn-safe offline harness before treating any test failure as behavioral RED; no real service or credential use in offline tests. | GATE |
| C-4 | Obtain and record the owner's answer: **May the named Azure subscription and ElevenLabs account fund these specific synthetic AC-09 probes?** An unanswered or negative Q1 blocks live execution and release acceptance, not offline work. Google follow-up requires applicable approval separately. | GATE |
| C-5 | Preserve the frozen surfaces and existing provider contracts. If implementation requires changing them or enforcement infrastructure, stop and return that scope for judgement/human review rather than extending this authority. | GATE |
| C-6 | Record the revised acceptance evidence before release. Tags, publication, consumer rollout and production use remain separately authorized actions. | GATE |

Authority granted: none by this advisory draft; after human review and incorporation of all required revisions, only the frozen standalone CLI, focused tests and directly associated documentation/version/evidence work may be implemented.
