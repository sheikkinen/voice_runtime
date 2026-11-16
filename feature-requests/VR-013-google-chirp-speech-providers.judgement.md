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
