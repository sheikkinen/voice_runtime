# Judgement: VR-013 Google Chirp 3 STT and TTS providers

**Verdict:** APPROVED WITH REVISIONS - the isolated provider extension is sound, but pacing, retry accounting, live-probe validity, configuration and lifecycle witnesses require R-1 through R-6; this DRAFT is advisory and grants no implementation authority before human review and incorporation of the revisions.

**Reviewed against:** Committed artifacts only. `VR/` means `/Users/sami.j.p.heikkinen/src/yamlgraph/projects/voice_runtime` at `4d24a505633d3edc944270c996b86b55431381a3`; `CSAP/` means `/Users/sami.j.p.heikkinen/src/customer-service-agent-platform` at `a60fb2b9bb42682fbcfe8e8dd5f7d26ebb8739d2`; `YG/` means `/Users/sami.j.p.heikkinen/src/yamlgraph` at `839e2819051cdd6268343e719d1d8977aa3a138b`.

- Request and evidence: `VR/feature-requests/VR-013-google-chirp-speech-providers.md` (abbreviated `FR` below); `VR/feature-requests/evidence/VR-012-research.md` (`Research`).
- Prior doctrine: `VR/feature-requests/VR-012-google-chirp-stt-tts-providers.judgement.md` (including its human promotion record and successor partition); `VR/feature-requests/VR-005-stt-session-task-ownership.judgement.md`; `VR/feature-requests/VR-009-azure-stt-language-identification.judgement.md` (current judgement and displayed historical excerpt).
- Cited implementation evidence: `VR/voice_runtime/providers/__init__.py`; `VR/voice_runtime/providers/azure_stt.py:1-280`; `VR/voice_runtime/providers/azure_tts.py`; `VR/voice_runtime/providers/elevenlabs_tts.py`; `VR/voice_runtime/session.py:1-290`; `VR/voice_runtime/stt.py`; `VR/voice_runtime/tts.py`; `VR/scripts/probe_vr009_lid.py`; `VR/pyproject.toml`.
- Governing doctrine: `CSAP/.github/skills/judge-fr/doctrine.md`; `CSAP/.github/skills/judge-fr/judgement.template.md`; `CSAP/.github/copilot-instructions.md`; `YG/.github/copilot-instructions.md`; `VR/reference/release-checklist.md`.

The target repository was clean; consumed CSAP/YAMLGraph doctrine files were unmodified. No author transcript, working notes, credentials, external web content or live service was consumed. Historical CSAP claims are assessed as claims in committed evidence, not independently verified field observations. No judge was launched. This requested temporary draft does not replace a canonical judgement beside the FR.

## Human promotion record — 2026-11-16

Round 1 was rendered by the YAMLGraph judge adapter against FR commit `4d24a50`, through the CSAP route (`scripts/judge.sh`, backend copilot, model `gpt-6.1-sol`); the yamlgraph adapter's pin `gpt-5.6-sol` is not available. The text above and below this section is the draft verbatim. The owner (Sami Heikkinen) reviewed the APPROVED WITH REVISIONS verdict on 2026-11-16 and accepted it ("fold").

Disposition (C-1): R-1 to R-6 and the revised acceptance criteria are folded into [VR-013](VR-013-google-chirp-speech-providers.md). Implementation authority covers only the frozen offline scope. The owner gates Q1 (TEST-project live probes), Q2 (forced-rotation loss bound) and Q3 (`publish.yml` diff) remain open and block AC-12, release and D-6.

## What is sound

The successor respects the parent split: vendor providers and direct probes remain together, while the independent cross-provider CLI is excluded (`FR:8,148-157,269-280`). Two implementations behind existing protocols are one vendor-integration concern, not grounds for another SPLIT. Lazy imports, unchanged transports/session, ADC, transcript-free STT logs and explicit consumer-timing limitations fit the existing boundaries (`FR:20-22,54-75,141-167`; `providers/__init__.py:23-57`; `stt.py:10-49`; `tts.py:6-25`).

The rotation design now names a response-independent deadline, bounded drain, serialized ownership, retained unsent bytes and explicit loss limits. Those are substantive improvements over a restart log masquerading as continuity evidence (`FR:77-117,195-203`). Raw streaming TTS bytes are preserved, and interruption, disconnect and optional stop-event cases have direct witnesses (`FR:123-139,209-217`). The SDK/Python evidence gap is acknowledged rather than hidden (`FR:37,222-230`; `Research:114-116`).

The strongest case against approval is that the named consumer still cannot safely switch on the basis of interface conformance, and backlog catch-up and forced rotation remain unproven. That does not justify rejecting an optional library backend: the FR explicitly leaves CSAP timers and field acceptance outside this release and gates billable probes and loss acceptance on human decisions (`FR:11,22,257-267`). It does justify withholding authority until the precise defects below are folded.

| Rubric criterion | Assessment and evidence |
|---|---|
| 1. Scope | Clear and substantially minimal after the parent split. Keep Google-only probes, not a second diagnostic product (`FR:8,148-167,271`). |
| 2. Consistency | The three-reconnect rule conflicts with AC-07's failure sequences; EOF is both normal completion and nominally retryable; pacing departs from the parent judgement (`FR:82-84,104-108,204-208,254`; parent judgement:66-68). R-1 and R-2 resolve these. |
| 3. Measurability | Most callback/frame assertions are mechanical. Gap-free numbered sentences do not guarantee a forced rotation, and printed IDs alone do not prove complete phrases survived (`FR:33-34,88-89,152-156,231-239`; `Research:85-89`). R-3 is required. |
| 4. Feasibility | Service/SDK evidence supports the direction, but neither a supported 2x rate nor the implemented async long-session path is proved. Exact-floor checks on supported Python versions remain proper enforcement gates (`FR:35-37,62,222-239`; `Research:95,116`). |
| 5. Architecture alignment | Existing protocols, lazy factories, owned tasks and a shared initial-request builder fit. Do not solve consumer timing with protocol changes, or vendor pacing uncertainty with speculative catch-up (`FR:54-62,77,143,159-167`; `azure_stt.py:119-140,197-225`). |
| 6. Single responsibility | One Google backend integration, including its STT/TTS halves and necessary evidence. No new orthogonal concern remains (`FR:4,8,141-157,269-280`). |
| 7. Strategic classification | **Contrib/provider extension**: one named first consumer, CSAP, needs another implementation of existing abstractions. Not a framework primitive, generic failover engine or documentation-only pattern (`FR:11,18-22,274`). |
| 8. Testability | Factory-based behavioral RED avoids missing-implementation imports. Deterministic retry, environment-isolation and worker-cleanup assertions need the refinements below (`FR:171-178,204-226`; judge doctrine, criterion 8). |

## Required revisions

### R-1: Freeze pacing at the evidenced real-time rate

Replace the 2x catch-up rule in the approach, D-1 and AC-04 with **at most 1x real time**, including after reconnect and rotation (`FR:82,188,254`). The committed evidence establishes 800-byte requests every 100 ms and an approximately real-time service constraint, not acceptance of 2x (`Research:62-64,95`). The parent explicitly required real-time resumption (parent judgement:66). This FR already permits the judge to select 1x.

Use monotonic scheduling: successive full 800-byte audio requests are separated by at least 100 ms; a delayed scheduler does not emit a burst to repay elapsed deadlines. State how the final short EOF batch is paced by its audio duration. Retain all unsent audio in order; document that an interruption leaves accumulated delay rather than claiming bounded catch-up latency. Correct the backlog distinction: a full 800-byte batch is also the normal send unit, so buffer size alone is not proof of outage backlog.

AC-04 must assert timestamps as well as payloads for both a preloaded 40-frame backlog and normally arriving frames, plus delayed scheduling and a short EOF batch. A higher catch-up rate needs separately judged service evidence, not an enforcer's discretion.

### R-2: Make reconnect counting and normal EOF unambiguous

Keep the approach's rule: **one initial failed stream plus three failed reconnect attempts**, four failed opens/streams total before exhaustion (`FR:108`). Replace AC-07's ambiguous "three consecutive failures" and its abbreviated reset example (`FR:206-207`) with numbered stream-attempt sequences:

- Initial failure, replacement 1 failure, replacement 2 failure, replacement 3 failure: four opens, backoff delays of 1, 2 and 4 seconds, then one exhausted callback; no fourth replacement.
- After either a delivered response or five error-free seconds on a replacement establishes success, a later failure starts a new allowance of three replacement attempts. Assert both reset mechanisms separately.
- Choose and document one stable exhausted-error suffix for unexpected EOF, and assert it; EOF has no existing exception class name to substitute.

Explicitly exclude **normal request EOF after the consumed `None` sentinel** and intentional rotation completion from unexpected response EOF (`FR:84,104`). Normal EOF flushes retained bytes, drains responses for at most five seconds, cancels a stalled call, then exits without reconnect or `on_error`. An unsolicited server EOF remains retryable. Add witnesses for both, terminal errors during replacement open/drain, and rotation/retry collision; assert the actual stream sequence and callback count. Preserve stop precedence and at most one open stream.

Correct `FR:117`: loss is measured by **AC-12**, not the TTS AC-08, and release acceptance belongs to **Q2**, not a nonexistent C-2 in the FR.

### R-3: Specify a live probe that proves the claimed rotation path

Replace the underspecified probe recipe (`FR:151-157`) with these executable requirements:

- Implement an explicit loop-less capture session that records `GoogleTTS` outbound bytes exactly once. An unmodified `VoiceSession.put_outbound_sync()` drops them without a loop (`session.py:145-149`), and the cited VR-009 script actually calls the Azure SDK directly, not a capture session (`probe_vr009_lid.py:54-64`). Assert nonempty 8 kHz mu-law capture before feeding STT.
- Give each **occurrence** a unique spoken ID attached to the same sentence as its expected content; do not loop reusable IDs or use `"Lause <n>."` as a separate sentence. The research demonstrates sentence-separated finals and number normalization (`Research:85-89`). Record ordered full normalized phrases as well as IDs, allowing ordered adjacent finals to reconstruct a phrase. An ID with missing phrase content must count as loss, not success.
- Schedule against actual stream-open monotonic time. The ordinary run must observe `forced=false` at the first qualifying final after 270 seconds. The forced run must actually observe `forced=true` at 290 seconds on the stream under test; concatenating punctuated TTS sentences without gaps does not establish that no final arrives between 270 and 290 seconds. If ordinary rotation preempts it, report the forced scenario as unexercised and fail that acceptance run; do not disable ordinary rotation or shorten production thresholds to obtain the live witness.
- Define separate run modes, exact invocation examples, pre/post-boundary phrase schedules, a finite overall timeout and bounded cleanup. Record observed stream-open/rotation times and commits beyond the tested boundary. Missing rotations, provider errors, timeout and empty capture are failures, not successful evidence.

AC-12 must require complete expected/observed phrase sequences, normalized-content omissions/duplicates and their time intervals, error output, rotation classifications, and TTS first-chunk latency measured at the first nonempty synthesis response rather than after `speak()` returns. If the forced path cannot be exercised with the proposed synthetic stimulus, release remains blocked pending an amended, judged probe plan.

Retain Q1. Make Q2 an explicit human question: **What measured maximum missing content/time interval at a forced boundary is acceptable for this library release, and do the recorded runs meet that bound?** Record the answer and comparison; no answer, unbounded loss or incomplete evidence blocks release. This is not permission to change CSAP safety policy or add replay.

### R-4: Define the public configuration boundary completely

The environment table does not define the constructor type/normalization of `phrase_hints`, and TTS inherits a missing-project rule referring to `start()`, which it does not have (`FR:43-52,121`; `providers/__init__.py:43-57`). Fold in:

- `phrase_hints: Sequence[str] | None`: `None` reads comma-separated `GOOGLE_STT_PHRASES`; explicit sequences override it; explicit empty sequences disable adaptation. Strip entries, drop empty entries, preserve order, and reject non-string entries with `ValueError`. A string is the environment representation, not an implicitly character-split constructor sequence.
- Explicit non-`None` scalar arguments override environment values; document the existing environment/default precedence for both providers. Specify treatment of empty project values consistently: use the first nonempty configured environment project, while an explicitly supplied empty project fails validation.
- Missing project raises `ValueError` before an SDK request at STT `start()` or TTS `speak()`; preserve TTS's no-request early returns for blank input, pre-disconnect and already-set interruption.
- State the exact TTS endpoint and quota-project construction, endpointing-value-to-real-SDK-enum mapping for all three allowed settings, and default voice/language derivation. Do not broaden the set of supported models, regions or voices.

Extend AC-03/AC-08 with environment-versus-constructor precedence, empty hints, environment comma normalization, invalid hint types, missing project and all three endpointing mappings, plus TTS endpoint/quota project/default voice. Keep these tests synthetic and use real SDK message schemas.

### R-5: Make the TTS cancellation deadline and ownership contract agree

The approach permits polling for up to 50 ms and then joining for up to one second, while AC-08 requires return within one second **of the event**, with the worker ended (`FR:131,137-138,216`). A bounded `join()` alone does not establish that a thread stopped; "cannot still emit audio" is weaker than "has ended," especially when the worker only queues chunks.

Specify one end-to-end monotonic cancellation budget of one second from the stop event, including polling, RPC cancellation and worker completion, not additive timeouts. The response worker only queues data/status; only `speak()` emits frames. Every exit after worker creation must cancel/finish its call and join the worker; no daemon-thread escape, detached work or silent join timeout is authorized. Queue delivery must distinguish audio, successful EOF and synthesis failure so a worker exception cannot leave the caller polling forever.

Extend AC-08's blocked-read witness to a blocked-read disconnect and worker-side API failure, asserting that the worker has ended and no outbound frame, tap or mark appears after return. Apply the same cleanup assertion to normal completion. Caller-requested cancellation returns the interrupted/disconnected result, not a spurious Google API failure callback. Keep the existing mark-wait behavior and public protocol unchanged; do not expand this into session-level mark cancellation.

### R-6: Make the validation matrix independent of test order

AC-02 cannot safely inspect the suite process's `sys.modules`: Google-dependent test modules intentionally import the real SDK during collection (`FR:175-178`). Require its lazy-import assertion in a fresh subprocess with controlled environment values, creating both Azure STT and TTS providers without network access. Run this assertion in both Google-present and Google-absent environments.

Amend AC-10 to run the **full offline suite**, not only AC-03 through AC-09, in fresh Python 3.11 and 3.12 environments with the exact Google floors and existing provider extras. Keep the separate never-installed-Google full-suite runs. Google-specific NC-165 conformance cases must use the same explicit optional-SDK policy without skipping existing provider cases or making collection depend on Google being installed.

Record reproducible install/test commands, interpreter and resolved SDK versions, exits and outputs. Google-present runs must execute the new tests without optional-Google skips; absent-Google runs skip only the new SDK-dependent cases while retaining isolation and existing-provider assertions. Neither offline environment may use ADC or make live API requests.

## Scope is frozen

This is the proposed implementation boundary after human promotion and the revisions, not present implementation permission.

| Deliverable | Surface |
|---|---|
| D-1 | Revised `feature-requests/VR-013-google-chirp-speech-providers.md`, its implementation/evidence record and directly cited sanitized research updates. |
| D-2 | `voice_runtime/providers/google_stt.py` and `google_tts.py`; Google branches/docstrings in existing `voice_runtime/stt.py` and `tts.py`. |
| D-3 | `tests/test_vr013_google_stt.py`, `tests/test_vr013_google_tts.py`, a non-SDK isolation witness and directly necessary factory/NC-165 conformance cases only. |
| D-4 | `scripts/probe_vr013_sdk_surface.py` and `scripts/probe_vr013_google_session.py`, including bounded synthetic capture, feeding and result checks; no shipped diagnostic CLI. |
| D-5 | `pyproject.toml` Google extra and next-minor version; directly related `README.md`, `.env.example` and `CHANGELOG.md` updates. |
| D-6 | Only the specified Google-extra addition to the test-job install line in `.github/workflows/publish.yml`, conditional on prior human review of its exact diff and Q3 approval. |

Not authorized: VR-014 implementation; CSAP or black-box repository edits, deployment, telephone calls or consumer-pin changes; timers, callbacks, session/transport changes or existing-provider refactors; Terraform/IAM/resource-policy changes; automatic failover or language identification; 2x/burst catch-up; overlapping streams, replay or deduplication; container sniffing/parsing or non-streaming TTS; shared provider frameworks; unrelated CI/hooks/doctrine edits; real caller/patient audio; credential inspection/storage/logging; tags or publication. No live probe or CI change is authorized while preparing FR revisions.

## Revised acceptance criteria

- [ ] AC-01: Test-only RED precedes GREEN through the existing factories, at exact SDK floors, using real messages/enums and fake clients. The original factory `ValueError` or behavioral assertion is the failure, never collection, fixture or dependency failure. Every new/changed VR-013 behavior test has `@pytest.mark.req("VR-013")`.
- [ ] AC-02: SDK-dependent cases use only the explicit optional-Google skip policy. Fresh-process lazy-import witnesses pass with and without Google installed; core/factory imports and Azure construction load no `google.cloud` module.
- [ ] AC-03: Initial and replacement STT requests contain the frozen recognizer, 8 kHz mono MULAW decoding, model, language, VAD/interim flags, endpointing enum and conditional normalized adaptation. R-4 configuration precedence, endpoint/quota project and validation cases pass.
- [ ] AC-04: Twenty-five frames produce five ordered 800-byte requests; speaking frames are silence; three frames at EOF produce 480 bytes. Fake-clock timestamps prove R-1 real-time pacing with ordinary input, queued backlog, delayed scheduling and the final short batch; rotation/reconnect never burst retained audio.
- [ ] AC-05: Original stripped final/interim/BEGIN callbacks, speaking/echo discard and exact lag/log assertions pass. Every final consumes END state, and replacement clears it. No transcript is logged.
- [ ] AC-06: Fake streams prove ordinary rotation after 270 seconds, forced rotation at 290 seconds without frames/responses, five-second maximum drain, retained partial batches/queued audio exactly once, actual stream serialization, replacement/drain errors and stop during opening/drain/backoff. Normal input EOF drains and exits without reconnect; client transport closes and no owned work survives stop.
- [ ] AC-07: Every declared retryable/terminal class and unsolicited EOF is exercised at the relevant open/read seams. R-2's explicit attempt sequences, 1/2/4-second backoff, both success-reset mechanisms, stable EOF reason and exactly-once terminal callback pass; normal EOF and successful rotation do not produce errors.
- [ ] AC-08: Original TTS result-table cases pass, including raw 333/160/7-byte chunks containing `RIFF`/`0xFF`, exact normal-only padding, one normal mark, optional stop event, errors, mark timeout and post-mark disconnect. R-4 TTS configuration and R-5 overall cancellation deadline, terminal-status propagation, worker termination and no post-return audio/tap/mark assertions pass.
- [ ] AC-09: All three Google factory paths return the correct provider types; conditional NC-165 conformance cases exercise the existing protocols. Existing provider cases remain active with unchanged assertions.
- [ ] AC-10: R-6's fresh Python 3.11/3.12 full-suite matrix passes with exact advertised Google floors and separately with Google never installed; the complete SDK-surface probe passes at those floors. Commands, interpreter/SDK versions and outputs are recorded. Any lowered floor needs the same exact-version proof.
- [ ] AC-11: `ruff check voice_runtime/`, package build and `twine check` pass. A clean non-editable wheel install with `[google]`, outside the checkout, constructs both providers through public factories without making live requests.
- [ ] AC-12: After affirmative Q1 approval, R-3's separately invoked ordinary and forced live scenarios exercise the real async STT provider and GoogleTTS in the named TEST project. Both rotation paths, full phrase continuity/loss, timings, errors, versions, SHA and commands are recorded. Any provider/region error, unexercised path, ordinary-rotation omission/duplication, or loss outside the explicit human Q2 bound blocks release.
- [ ] AC-13: README and commented environment examples document the extra, ADC/quota project, exact configuration, Chirp 3 HD restriction, callback-cadence difference, lag semantics, real-time backlog delay and rotation loss. Changelog/version use the actual next minor. D-6 is changed only after Q3 approval; tags/publication remain outside this FR.
- [ ] AC-14: The FR records folded revisions, RED/GREEN commits, implementation decisions/deviations, full-suite and artifact evidence, live results and owner answers. Unanswered/declined gates are recorded as blocked, not fulfilled.

## Conditions for enforcement

| # | Condition | Severity |
|---|---|---|
| C-1 | A human reviews/promotes this draft and R-1 through R-6 are folded into the committed FR before implementation authority activates. Until then this is advisory only. | GATE |
| C-2 | Q1 must explicitly authorize the named project's billable synthetic probes before any live request. Past research permission is not new permission; do not access credentials or live services during judgement/revision preparation. | GATE |
| C-3 | The human answers the revised Q2 with a measurable accepted loss bound and accepts the recorded comparison before release. Missing evidence, an unexercised forced path, unbounded loss or a failed live gate leaves release blocked. | GATE |
| C-4 | Q3 requires human review and approval of the exact one-line workflow diff before that change. No other CI, permissions, secrets, triggers, hooks or enforcement behavior may change. | GATE |
| C-5 | Preserve existing protocols/providers/session/transports, serialize STT stream ownership, retain only the stated unsent-audio guarantee and prove task/thread cleanup. Offline RED and acceptance must not make network requests. | GATE |
| C-6 | CSAP rollout remains separately blocked on its judged timer/field acceptance work. Library approval never constitutes consumer safety acceptance. | GATE |
| C-7 | Tagging, publication and deployment require separate explicit human authority; use the actual next-minor release rule and all required package/live evidence. | GATE |

Authority granted: none by this advisory draft; after human promotion and committed R-1 through R-6, authority may cover only the frozen Google provider extension, offline tests, local artifacts and documentation, with live probes, CI edits and release still subject to their separate human gates.

## Round 2 (Amendment A-1) — human decision record — 2026-11-16

The YAMLGraph judge route (CSAP `scripts/judge.sh`) re-judged Amendment A-1 at voice_runtime `cdb504e`. Draft verdict: APPROVED WITH REVISIONS, R-1 (restate the Q2 rationale as an explicit owner waiver of unmeasured forced-swap loss), R-2 (live probe must prove post-swap transcription) and R-3 (cancel-plus-join budget of 950 ms after detection).

Owner decision (Sami Heikkinen):

- **Draft not promoted.** None of R-1 to R-3 is folded; Amendment A-1 and the FR stand as committed at `cdb504e`. The round-2 draft is advisory and is not adopted.
- **Waiver accepted.** The owner accepts that a forced swap may lose an in-flight utterance, including content spoken before the cut, unmeasured and without a loss bound. This supersedes round-1 C-3 for this library release.

## Round 3 (Amendment A-2) — human promotion record — 2026-11-16

The YAMLGraph judge route (CSAP `scripts/judge.sh`) judged Amendment A-2 at voice_runtime `8085094`. Draft verdict: APPROVED WITH REVISIONS, R-1 to R-5. The draft is reproduced verbatim below; only its heading levels are lowered by two so that it nests in this file.

Owner decision (Sami Heikkinen):

- **Promoted:** R-1 (honest wording, original FAIL preserved), R-2 (deterministic scorer in the probe, synthetic witnesses, committed run-1 evidence), R-4 (TTS event-to-return budget and explicit worker non-termination failure), R-5 (frozen-surface reconciliation and durable AC-10/AC-11 evidence).
- **R-3 not promoted; replaced.** No change to the probe tail-wait logic and no AC-16. Rationale: no re-run of the VR-013 live probe is planned (owner answer 3); natural-speech checks move to a csap-black-box end-to-end follow-up. Instead, the FR records that the cause of the two unmatched final words (`pallo`, `nukke`) is **unconfirmed**. The D-4 phrase "bounded completion" and AC-12/AC-14 references to R-3 are void.
- AC-15 is adopted. AC-16 is dropped with R-3.

### Judgement: VR-013 Google Chirp 3 STT and TTS providers

**Verdict:** APPROVED WITH REVISIONS - the optional provider extension and owner-selected A-2 policy are sound directions, but acceptance requires R-1 through R-5; this DRAFT is advisory until human review and incorporation into the committed FR.

**Reviewed against:** Committed artifacts only. `VR/` means `/Users/sami.j.p.heikkinen/src/yamlgraph/projects/voice_runtime` at `808509449877348b9f617df0e5961c1bf611b6ab`; `CSAP/` means `/Users/sami.j.p.heikkinen/src/customer-service-agent-platform` at `a60fb2b9bb42682fbcfe8e8dd5f7d26ebb8739d2`; `YG/` means `/Users/sami.j.p.heikkinen/src/yamlgraph` at `ad5db79402238a70b4e7028759bf2d5d81e24edf`.

- Request: `VR/feature-requests/VR-013-google-chirp-speech-providers.md` (`FR` below), including implementation evidence, A-1/A-2 and the recorded owner decisions.
- Prior judgement: `VR/feature-requests/VR-013-google-chirp-speech-providers.judgement.md`, including round-1 promotion and the round-2 human decision record.
- Cited evidence: `VR/feature-requests/evidence/VR-012-research.md`; `VR/scripts/probe_vr013_google_session.py`; `VR/voice_runtime/providers/__init__.py`; `VR/voice_runtime/providers/google_tts.py:1-235`; `VR/voice_runtime/providers/google_stt.py:1-200,290-450`; `VR/voice_runtime/providers/_google_config.py`.
- Doctrine: `CSAP/.github/skills/judge-fr/doctrine.md`; `CSAP/.github/skills/judge-fr/judgement.template.md`; `CSAP/.github/copilot-instructions.md:1-100,210-end`; `YG/.github/copilot-instructions.md:1-231`; `VR/reference/release-checklist.md`.

The target working tree was clean, and the consumed CSAP/YAMLGraph doctrine files were unmodified. Repository identity/status checks were used only to establish committed provenance. The uncommitted `VR/tmp/vr013-live.log`, `tmp/matrix.sh`, `tmp/ac11.sh`, author conversations and working notes were not consumed. Their contents are not independently established by the FR's summaries. No live request, credential access, judge invocation or implementation was performed. This temporary draft does not replace the canonical judgement.

#### What is sound

The proposal remains one Google vendor integration behind existing STT/TTS protocols, not a new framework primitive or a second diagnostic product. Optional SDK imports, unchanged consumer protocols and transports, serialized streams, real-time pacing, synthetic-only probes and separate CSAP acceptance are appropriate boundaries (`FR:20-37,168-192,209-219,435-453`; `providers/__init__.py:23-57`).

A-2 correctly distinguishes spoken carrier text from punctuation: joining `kohta` to a word does not invent content. The committed examples identify meaningful raw-output surprises, including a word split between streams, substitutions and an unmatched tail (`FR:346-371,390-394`). The owner has explicitly chosen a reported-only match share, a three-consecutive-unmatched-word threshold and reuse of run 1 without a paragraph rerun (`FR:397-407`). Those product/spend decisions are not the judge's to replace with a new accuracy gate.

The strongest case against approval is that changing a scorer after observing a failure can turn a weak proxy into a false continuity claim. A-2 presently calls a threshold-based pass loss-free and attributes the missing tail to a confirmed probe defect without confirming evidence. This does not invalidate an optional backend, but it requires honest semantics, reproducible evidence and bounded completion before acceptance.

| Rubric criterion | Assessment and evidence |
|---|---|
| 1. Scope | One provider extension with necessary probes; retain the parent partition. A-2 needs only local scoring, completion and documentation changes, not a generic evaluator or new consumer rollout (`FR:209-219,374-409,455-end`). |
| 2. Consistency | The operative probe recipe and AC-12 still require exact words, whereas A-2 tolerates unmatched words. A-2 changes documentation while declaring AC-13 unchanged. Polling plus a fresh one-second budget also conflicts with AC-08's event-to-return bound (`FR:163-164,178-183,272-289,385-409`). R-1 and R-4 resolve these. |
| 3. Measurability | The threshold is countable only after reference construction, alignment and word attribution are fixed. A reported 91/98 cannot be recomputed from abridged sequences (`FR:335-345,366,378-394`). R-2 is required. |
| 4. Feasibility | The cited SDK research and recorded Python matrix support the backend. The existing probe has neither timestamped commits nor a completion-aware tail; three quiet seconds can precede outstanding audio (`FR:278-286,319-328,379-385`; probe:266-293). R-2/R-3 make the probe workable. |
| 5. Architecture alignment | Existing protocols suffice; private Google configuration extraction is defensible but must enter frozen scope. No session/protocol changes are needed for probe-local completion observation (`FR:413`; `_google_config.py:1-128`; `google_stt.py:128-173`). |
| 6. Single responsibility | STT, TTS and their witnesses remain one vendor integration. The independent cross-provider CLI, failover and consumer safety work remain excluded (`FR:8,209-219,455-end`). No further SPLIT is warranted. |
| 7. Strategic classification | **Contrib/provider extension**: one named first consumer, CSAP, using existing abstractions. Neither three independent use cases nor a missing framework abstraction is established (`FR:11,20-24`; `providers/__init__.py:23-57`). |
| 8. Testability | Existing factory-based RED avoids import/fixture failures. A-2 lacks synthetic witnesses for its new scorer and wait policy, and AC-08 lacks a worst-case cancellation witness implied by its contract (`FR:225-232,272-277,378-385`; `google_tts.py:34-35,199-207`). R-2 through R-4 define them. |

#### Required revisions

##### R-1: Fold A-2 into the operative contract and limit its claims

Replace the exact-sequence pass rules in section 4 and AC-12 with the owner's A-2 rule; update Status, AC-13 and Open risks consistently (`FR:5,178-183,287-301,374-409`). Preserve run 1's original exit code 1 and FAIL under the original scorer. Label any later result **retrospective PASS under A-2**, with a distinct scorer identity; never rewrite the original outcome.

State that the threshold detects an unmatched-word pattern, not the cause of that pattern. Dropped audio can remove one or two words, and misrecognition can affect three or more; the threshold cannot distinguish those phenomena by itself (`FR:383,400`). It also does not gate inserted text, duplicated content or aggregate quality. Document those limits without adding a new quality threshold.

Replace "without loss," "there is no loss at the rotation," "harmless" and the definitive tail-defect attribution with bounded observations: the displayed boundary word reconstructs after whitespace normalization; ordinary rotation can split an utterance; two final reference words were unmatched; their cause remains unconfirmed (`FR:178,355-358,386,390,393`). Downstream semantic harmlessness has not been demonstrated here. Retain the explicit owner forced-loss waiver (`FR:445`; prior judgement:148-155); do not revive the unpromoted round-2 revisions as authority.

Retain the recorded no-rerun decision. AC-12 may be completed from historical evidence only after R-2, not from the current unsupported aggregate. A paragraph/black-box quality run remains a separate consumer concern.

##### R-2: Specify and witness a deterministic scorer, then commit reproducible run-1 evidence

The instruction "align the character streams" does not specify an algorithm or how repeated carrier words are handled (`FR:378-379`). Fold a complete scoring contract into the FR:

1. Use Unicode case folding and explicitly defined whitespace/punctuation removal while retaining Finnish letters. Preserve a map from normalized reference offsets to the original phrase and target-word index.
2. Include the actually spoken `Kohta` carrier in each expected phrase. Count only the unique target words in the denominator, not the repeated carrier. Do not strip a guessed prefix from arbitrary recognized words.
3. Name the alignment algorithm, options and deterministic tie behavior. Disable any frequency-based heuristic that can discard repeated carrier characters. Define exactly when all target characters constitute a match, including insertions inside versus after a target and matches spanning adjacent commits.
4. Compute consecutive unmatched runs over the original target order, including leading and trailing unmatched words. Report inserted text and duplicates separately, with no extra release threshold. Bind the ordinary-rotation check to the actual stream-open time, not feed time.

Add a few synthetic, network-free tests in the existing VR-013 test surface, each marked `@pytest.mark.req("VR-013")`: joined carrier/punctuation and split-across-commit success; a genuine substitution remaining unmatched; trailing insertion reporting; two-versus-three consecutive unmatched words, including at EOF; repeated-carrier input long enough to expose alignment heuristics. Test the actual probe scorer, not constants or document wording.

Commit a sanitized evidence artifact under `feature-requests/evidence/` holding the complete ordered expected target list, complete original committed texts, original command/SHA/versions, observed stream/rotation records and errors. Add the exact offline rescore command, scorer SHA/options, per-word match flags, inserted text, longest unmatched run and totals. The original raw file is expressly uncommitted, and the excerpt omits the full lists (`FR:335-345,366,388-394`), so 91/98 is currently a claim, not an auditable result.

For the requested +/-5-second boundary report, distinguish reference-audio time from commit-receipt time and use actually recorded timestamps. The current probe stores neither phrase offsets nor commit timestamps (`probe:241-293`). Record unavailable historical timing as unavailable; do not infer it from word order. A future-run boundary report can use new probe-local timestamps, but no new live run is required solely to backfill historical timing. Keep AC-12 blocked if complete historical sequences cannot be recovered; only a human can then authorize another run or an explicit evidence waiver.

##### R-3: Make the tail wait completion-aware and bound the entire probe

Three seconds without a commit after enqueueing `None` does not prove queued audio has reached the SDK or response draining has finished (`FR:385`; probe:284-293). A five-second rotation drain alone can exceed that quiet interval, and 1x pacing preserves the resulting backlog (`FR:435`).

Use probe-local observation of the existing STT owned-task lifecycle: wait for natural input/response completion before treating a three-second quiet interval as completion. Cap the post-sentinel phase at 15 seconds. If the owner task remains active at the cap, record a tail-completion timeout and fail the prospective run, then perform bounded cleanup. Do not change the public protocol, production thresholds or force `stop()` merely because an active stream has been quiet. Preserve and surface provider task exceptions rather than reporting them as successful completion.

Define whether the 360-second overall budget includes synthesis, streaming and the tail; include them, with separately bounded 15-second teardown. The current timeout wraps only `run_stt`, leaving synthesis outside it (`probe:321-340`; `FR:181`). No detached synthesis worker or abandoned thread is authorized.

Add synthetic probe tests for a quiet interval while audio remains pending, commits arriving after that interval, natural completion and expiration of the tail/overall deadlines. Keep the retrospective run-1 score distinct from validation of this changed wait policy: an offline rescore cannot prove the tail fix worked live or explain the missing final words.

##### R-4: Restore the already-frozen TTS event-to-return and worker-ownership contract

This is an unresolved contradiction in the submitted FR, not permission to import the unpromoted round-2 verdict. AC-08 measures one second from the event, but section 2 grants one second after detection plus up to 50 ms polling (`FR:163,272-277`). The cited implementation starts that full second after `_pump` returns and logs a live worker before returning a normal interruption/disconnection result (`google_tts.py:34-35,199-215`), contrary to the FR's no-escape requirement (`FR:164`).

Fold an event-to-return budget that reserves polling time: at most 950 ms for cancellation/join after detection, with the existing at-most-50-ms polling allowance included in the one-second total. Cancellation itself must be covered, not just `join`. Add a worst-case synthetic blocked-read witness with delayed cancellation/worker exit and an event arriving immediately after a poll; cover both stop and disconnect, and assert total elapsed time and worker termination.

Require explicit failure rather than a success-shaped return when a worker does not terminate. Do not claim worker cleanup fulfilled merely because an error was logged. If the SDK cancellation path cannot satisfy bounded return and terminated ownership together, leave AC-08/release blocked and amend the lifecycle design; do not use daemon threads, detached work or silently relax the contract. Any implementation correction is limited to this existing Google TTS cancellation/cleanup seam and its tests.

##### R-5: Reconcile frozen surfaces and complete durable evidence

Fold `_google_config.py` into D-2 as a private extraction of the already-authorized configuration/request builder, not a shared framework. Add the already-recorded requirement-marker registration in `tests/conftest.py` to D-3 (`FR:209-219,413-414`). Include synthetic probe tests and the sanitized evidence artifact authorized by R-2/R-3.

AC-10 requires exact install/test commands, resolved versions and results, but the record currently names uncommitted scripts and abbreviated summaries (`FR:278-282,319-328`). Copy the relevant commands, interpreter/SDK resolution excerpts, exit codes and summaries into committed evidence; do not consume or rely on temporary scripts as lasting proof. Existing recorded results need not be rerun just to transcribe recoverable evidence, but any production correction in R-4 must rerun the affected checks and package validation.

Update AC-14 and Status to distinguish completed original criteria, pending amendment tests, retrospective scoring and outstanding lifecycle evidence. AC-13 cannot remain fulfilled for newly required ordinary-rotation documentation before that documentation exists. Record the exact effective workflow line with the retained `-e`, and preserve its human-review gate; no additional CI or hook change is authorized (`FR:415,446-447`).

#### Scope is frozen

The following supplements the previously promoted scope; it is not present implementation authority.

| Deliverable | Surface |
|---|---|
| D-1 | VR-013 FR, its canonical judgement after human promotion, and sanitized `feature-requests/evidence/VR-013-*` scoring/validation evidence. |
| D-2 | Existing Google STT/TTS provider surfaces and factories, plus private `voice_runtime/providers/_google_config.py`; new production correction limited to R-4 Google TTS cancellation/cleanup. |
| D-3 | Existing VR-013 provider/lazy-import tests and directly necessary factory/conformance cases; `tests/conftest.py` marker registration; synthetic probe scorer/wait witnesses in the VR-013 test surface. |
| D-4 | `scripts/probe_vr013_sdk_surface.py` and `scripts/probe_vr013_google_session.py`; local deterministic scoring, timestamps and bounded completion only. |
| D-5 | Already-authorized Google extra/minor-release metadata, directly related README/environment examples/changelog; honest ordinary/forced rotation limitations. |
| D-6 | Previously owner-approved Google-extra addition to the test-job install line in `.github/workflows/publish.yml`, retaining `-e`; human review remains required. |

Not authorized: generic scoring/evaluation frameworks, VR-014, CSAP/black-box edits or calls, deployment or consumer pins, failover, new callbacks or protocol/session/transport changes, existing-provider refactors, IAM/Terraform/region-policy work, faster catch-up, overlapping streams, replay/deduplication, speech-quality thresholds invented by the enforcer, non-streaming TTS, real caller/patient audio, credential inspection/storage/logging, additional CI/hooks/doctrine changes, tags or publication. No new live request is required or authorized by this judgement.

#### Revised acceptance criteria

- [ ] AC-01: Preserve the original factory-based behavioral RED/GREEN record and VR-013 requirement markers. Amendment scorer/wait and any R-4 behavior correction have focused failing synthetic witnesses before their fixes; no collection, fixture or dependency failure counts as RED.
- [ ] AC-02: Preserve optional-Google skips and fresh-subprocess lazy-import witnesses, with and without Google installed. Existing provider tests remain active.
- [ ] AC-03: Preserve initial/replacement STT request, SDK enum, hints, endpoint/quota project, precedence and validation witnesses.
- [ ] AC-04: Preserve actual-byte and fake-clock 1x pacing witnesses for normal input, backlog, delayed scheduling, short EOF and replacements.
- [ ] AC-05: Preserve callback/discard/lag and transcript-free production-log witnesses; reconcile the recorded speaking-time callback policy explicitly in the FR.
- [ ] AC-06: Preserve actual stream-sequence witnesses for ordinary/forced rotation, bounded drains, unsent-byte retention, serialized ownership, EOF and stop precedence.
- [ ] AC-07: Preserve four-failed-stream exhaustion, 1/2/4-second delays, both success-reset mechanisms, terminal errors and normal EOF/rotation witnesses.
- [ ] AC-08: Preserve the original TTS result/frame/mark cases; R-4's worst-case stop/disconnect witnesses prove event-to-return <=1 second, terminated workers and no post-return frames/taps/marks. Nontermination is explicit failure, never successful interruption.
- [ ] AC-09: Preserve all Google factory and conditional protocol-conformance cases without weakening existing provider assertions.
- [ ] AC-10: Preserve the fresh Python 3.11/3.12 exact-SDK-floor and Google-never-installed matrix; commit the actual commands, versions, exits and summaries required by R-5.
- [ ] AC-11: Preserve ruff/build/twine and outside-checkout non-editable wheel-factory evidence; refresh affected package evidence after production correction.
- [ ] AC-12: Using committed complete run-1 evidence and the deterministic A-2 scorer, record the original FAIL separately from retrospective acceptance. Pass only with no recorded provider/region error or timeout, one ordinary rotation after 270 seconds of actual stream age, no forced rotation, and longest consecutive unmatched target-word run <3. Report match share and insertions without a quality gate. Record historical timing gaps honestly; no loss-free claim or paragraph rerun requirement is added.
- [ ] AC-13: README/Open risks document ordinary utterance splitting, heuristic limitations and unmatched-tail uncertainty alongside existing callback cadence, 1x backlog and owner-waived forced loss. Existing packaging/auth/configuration documentation and next-minor policy remain; D-6 retains exact human-reviewed scope.
- [ ] AC-14: The FR records folded revisions, amendment commits, durable original/offline-rescore evidence, decisions, deviations and all pending/fulfilled gates accurately. Unsupported results remain pending.
- [ ] AC-15: Synthetic tests exercise R-2's actual scorer and prove normalized join/split handling, genuine unmatched content, insertion reporting and two-versus-three consecutive unmatched targets including EOF. The offline rescore emits reproducible per-word flags and totals from complete committed sequences.
- [ ] AC-16: Synthetic tests exercise R-3's actual completion/wait policy: quiet pending audio is not completion, late commits are retained, natural completion succeeds, and tail/overall timeout fails with bounded cleanup. Prospective probe output timestamps phrase feeds and commit receipt separately.

#### Conditions for enforcement

| # | Condition | Severity |
|---|---|---|
| C-1 | A human reviews/promotes this DRAFT and R-1 through R-5 are folded into the committed FR before amendment authority activates. | GATE |
| C-2 | The owner's recorded reported-only quality metric, three-word threshold and no-rerun decision are preserved. They waive strict continuity acceptance, not evidence integrity or consumer safety; no new spend/policy decision is made by the agent. | GATE |
| C-3 | AC-12 stays pending until complete committed historical evidence is reproducibly scored. If it cannot be recovered, record blocked and obtain a human evidence waiver or separately approved new run; never silently substitute summaries. | GATE |
| C-4 | SDK/task/thread ownership and the frozen cancellation contract must be met. A logged surviving worker or an unconfirmed tail explanation cannot satisfy acceptance. | GATE |
| C-5 | No network/ADC in offline RED or amendment tests; no live services or credentials during judgement/revision preparation. Any future billable probe needs explicit human authority for that run. | GATE |
| C-6 | The sole workflow-line change remains subject to human review of the exact effective diff. No enforcement-infrastructure expansion or gate bypass is permitted. | GATE |
| C-7 | Preserve the explicit forced-loss owner waiver; CSAP rollout, timers and field-quality acceptance remain separately gated. This draft grants no deployment, tagging or publication authority. | GATE |

Authority granted: none by this advisory draft; after human promotion and committed revisions, only the frozen local amendment surfaces and the narrowly required Google TTS contract correction may be enforced, with retrospective live acceptance dependent on durable evidence and all release/consumer actions separately gated.
