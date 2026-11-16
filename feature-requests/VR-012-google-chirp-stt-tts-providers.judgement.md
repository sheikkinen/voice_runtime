# Judgement: VR-012 Google Chirp STT/TTS providers and cross-provider speech check CLI

**Verdict:** SPLIT - the Google speech backend and the independently useful multi-provider diagnostic CLI are separate concerns; each must re-enter judgement in its own FR after the revisions below. This is a DRAFT advisory judgement, not implementation authority.

**Reviewed against:** The following committed artifacts only. Paths prefixed `VR/` resolve under `/Users/sami.j.p.heikkinen/src/yamlgraph/projects/voice_runtime` at `bee762344914f4d039db7615f237b1d6502abde0`; paths prefixed `CSAP/` resolve under `/Users/sami.j.p.heikkinen/src/customer-service-agent-platform` at `a60fb2b9bb42682fbcfe8e8dd5f7d26ebb8739d2`.

- `VR/feature-requests/VR-012-google-chirp-stt-tts-providers.md` and `VR/feature-requests/evidence/VR-012-research.md`.
- Cited implementation evidence: `VR/voice_runtime/providers/__init__.py`; `VR/voice_runtime/providers/azure_stt.py` (lines 1-280); `VR/voice_runtime/providers/azure_tts.py`; `VR/voice_runtime/providers/elevenlabs_stt.py` (lines 1-280); `VR/voice_runtime/providers/elevenlabs_tts.py`; `VR/voice_runtime/stt.py`; `VR/voice_runtime/tts.py`; `VR/voice_runtime/session.py`; `VR/scripts/probe_vr009_lid.py`; `VR/pyproject.toml`.
- Release doctrine and prior judgements: `VR/reference/release-checklist.md`; `VR/feature-requests/VR-005-stt-session-task-ownership.judgement.md`; `VR/feature-requests/VR-009-azure-stt-language-identification.judgement.md` (current verdict and revisions, plus the displayed historical excerpt).
- Governing contract: `CSAP/.github/skills/judge-fr/doctrine.md`; `CSAP/.github/skills/judge-fr/judgement.template.md`; `CSAP/.github/copilot-instructions.md`.
- Enclosing-project doctrine: `/Users/sami.j.p.heikkinen/src/yamlgraph/.github/copilot-instructions.md` at `839e2819051cdd6268343e719d1d8977aa3a138b`.

The target repository was clean, and the consumed enclosing-project doctrine was unmodified. No author transcript, working notes, credentials, live service, or external web content was consumed. The CSAP timer claims and unavailable external history are treated as statements in the FR, not independently verified evidence. No judge was launched. This requested temporary draft does not replace or promote a canonical judgement beside the FR.

## Human promotion record — 2026-11-16

Round 1 was rendered by the YAMLGraph judge adapter against FR commit `bee7623`. The voice_runtime-enclosing yamlgraph adapter could not run: its pin `gpt-5.6-sol` reports "Model is not available", and the run produced no artifact. The identical CSAP route (`scripts/judge.sh`, backend copilot, model `gpt-6.1-sol`, session `8020c99c`) was used instead. The owner (Sami Heikkinen) reviewed the SPLIT verdict on 2026-11-16 and accepted it ("proceed as proposed").

Disposition (C-2): VR-012 is closed as **Split**. Its scope continues in:

- [VR-013](VR-013-google-chirp-speech-providers.md): Google Chirp 3 STT/TTS providers (D-2, D-5; R-2, R-3, R-4, R-6, R-7).
- [VR-014](VR-014-speech-check-cli.md): cross-provider speech check CLI (D-3; R-2, R-5, R-6, R-7).

Each successor needs its own judgement. The three R-7 owner questions are carried into the successors unanswered.

## What is sound

The Google-provider gap is real: the cited factories currently expose Azure, ElevenLabs and mock implementations, not Google. The committed research records working regional streaming requests, SDK versions, audio format and actual response timing, including the inconvenient absence of interim results (`VR-012-research.md:37-89`). Reusing the existing provider protocols and lazy factory imports is smaller than changing transports or the FSM (`VR-012:134-139,203-209`). Provider-local ownership, explicit terminal errors, stripped transcripts and transcript-free commit logs are good constraints to preserve (`VR-012:91-107`).

The strongest case for keeping the bundle is that the CLI would provide a convenient live acceptance tool for the new backend. That is not a functional dependency: the existing synthetic-probe pattern can verify Google, while the proposed CLI also solves an Azure/ElevenLabs diagnostic problem without Google (`VR-012:22-28,159-201`; `scripts/probe_vr009_lid.py`). Convenience does not justify coupling their release gates.

| Rubric criterion | Assessment and evidence |
|---|---|
| 1. Scope | Too broad: a vendor backend plus a shipped multi-vendor runner, audio-file support, scoring and machine-readable reporting (`VR-012:65-201`). Split as R-1. |
| 2. Consistency | Restart waits have no bound despite the forced-rotation deadline; the claimed interchangeable consumer behavior conflicts with the acknowledged timer limitation (`VR-012:28,96-99,302-306`). R-3 and R-7 close these claims. |
| 3. Measurability | Many SDK and callback assertions are precise, but stalled rotation/cancellation, CLI timeout enforcement and long-run loss detection have no sufficient witnesses (`VR-012:238-257,271-284`). R-3 through R-6 specify them. |
| 4. Feasibility | Regional service access is evidenced, but the live STT probe uses synchronous `SpeechClient`, whereas the design selects `SpeechAsyncClient`; the SDK probe ran on Python 3.14 rather than the CI floor (`VR-012-research.md:37-64`; `VR-012:85,285-289`). Feasible direction, incomplete proof. |
| 5. Architecture alignment | Existing protocols, lazy factories and the mixer capture hook fit. The speculative WAV fallback and unspecified mark-disconnect detection do not establish the promised boundary behavior (`VR-012:123-132,175-185`; `session.py:188-216`). R-4 removes the fallback and makes lifecycle behavior explicit. |
| 6. Single responsibility | The backend implements one vendor behind existing contracts; the cross-provider diagnostic product is independently shippable and testable (`VR-012:22-28,159-201`). SPLIT is required. |
| 7. Strategic classification | Backend: **contrib/provider extension**, with one external consumer, CSAP. CLI: **contrib/diagnostic utility**, with named release and incident uses; existing provider APIs suffice, so no framework primitive or new orchestration layer is justified (`VR-012:10,26-28,175,323`). |
| 8. Testability | Config, frames and callbacks admit direct tests. An import-caused RED is expressly allowed by AC-01, contrary to the judge rubric; cancellation and stream-transition witnesses are missing (`VR-012:213-247`; judge doctrine, criterion 8). R-2 through R-6 repair the test contract. |

## Required revisions

### R-1: Separate the provider integration from the diagnostic product

Replace this executable bundle with two independently judged FRs:

1. **Google speech backend:** Google STT/TTS providers, their existing factory entries, optional SDK dependencies, direct tests, documentation and Google-only synthetic acceptance probes. Keep the two providers within this vendor-integration concern; do not introduce a shared provider framework.
2. **Cross-provider speech check CLI:** packaged diagnostic entry module, capture, input conversion, WER, reporting and exit semantics using the existing public APIs. It must be independently testable with fakes and the already available providers; Google-specific live rows become applicable after that backend is available.

Move AC-09 and the CLI portions of AC-10/12/13 into the CLI FR. Replace the backend's dependency on the new CLI with a directly runnable synthetic probe following the cited existing probe pattern. Preserve the committed research by reference. Each successor owns its own RED/GREEN record, version/changelog changes and release gate; do not retain a shared all-or-nothing implementation gate (`VR-012:159-201,263-298`).

### R-2: Require behavioral RED and define optional-SDK test execution

Replace AC-01's permission for import failures with collected tests whose failing assertions expose absent behavior through existing factory/API seams. Missing implementation imports, missing fixtures or missing SDK installations are not acceptance evidence, even when caught inside a test (`VR-012:213-216`; judge doctrine, criterion 8).

Run backend RED with the exact Google SDK floors installed. Fake the clients, not the SDK message schemas or async iterator contract. In the clean no-Google environment, skip only SDK-dependent Google tests with an explicit optional-dependency reason; keep factory isolation and non-Google tests active. Do not blanket-skip the suite or fabricate the whole `google` namespace. Use the owning successor FR's requirement marker.

### R-3: Specify the STT stream-transition state machine, not just its trigger

The 290-second trigger cannot be guaranteed by checking responses alone, and closing a request generator does not bound the subsequent response drain (`VR-012:96-104,238-247`). Fold these rules into the backend FR:

- A session-owned monotonic timer triggers forced rotation at 290 seconds even when no response or inbound frame arrives. After 270 seconds, an eligible final result can trigger the ordinary rotation.
- Permit at most five seconds for the old response stream to drain, then cancel/close it before opening its replacement. Distinguish intentional rotation completion from unexpected EOF. A replacement-open failure follows the declared retry/terminal policy; it is not silently treated as a successful rotation.
- One owner serializes rotation and reconnect. Preserve unsent partial batches as well as queued frames, including a one-to-four-frame partial batch. Flush a final short batch on normal input EOF. Preserve real-time sending after reconnection rather than bursting a backlog. Stop and the `None` sentinel must prevent resurrection; close the SDK client/channel as well as session-owned tasks.
- Limit the no-loss/no-duplication claim to locally retained, unsent audio. Audio already handed to a failed RPC is not delivery-acknowledged, and byte counts cannot prove transcription continuity. Do not promise two successful half-utterance commits at a forced cut (`VR-012:90,242,306`).
- Define three failed reconnect attempts after the initial stream failure, reset only after a successful replacement, and emit the terminal callback once. Cover every declared retry class, unexpected EOF, failures while opening and iterating, and stop during backoff.
- Consume and clear the END timestamp on each final result, including empty/discarded results, and clear it across stream replacement. A later result must not inherit stale lag (`VR-012:105-106,237`).

Add fake-clock witnesses for a silent/stalled server, a blocked old-stream drain, partial-batch carry-over, a reconnect/rotation collision and stop in each transition. Assert actual stream sequence and retained bytes, not just the final task count.

### R-4: Remove speculative TTS format recovery and complete lifecycle acceptance

Remove the per-chunk `RIFF` sniff/strip and the alleged non-streaming fallback from both the approach and AC-07 (`VR-012:127,255,318`). The committed streaming evidence is raw mu-law (`VR-012-research.md:51-57`); arbitrary raw audio can contain those byte values, and a WAV header can span chunks. No non-streaming request is actually specified. Preserve all raw bytes, with padding only at normal completion; container support needs separately evidenced scope.

Make the existing protocol's `stop_event=None` behavior, empty input, pre-disconnected session, already-set stop event, mid-stream interruption, API failure and mark timeout explicit in the successor. All result branches retain `last_spoken` and the applicable existing flag/error keys. Check `session.is_disconnected` after the mark wait: that method unblocks on disconnect rather than raising a disconnect exception (`session.py:188-216`; `VR-012:129-132`).

Checking only between arriving chunks cannot interrupt a stalled iterator (`VR-012:128,256`). Require cancellation to end a blocked synthesis read within one second of the stop event, with no later outbound audio or mark and no owned watcher/worker left running. Exercise this with a fake whose next read waits until cancelled. Keep session, transport and existing provider implementations unchanged.

### R-5: Complete the CLI's externally observable contract

The CLI successor must fold these concrete corrections into its interface and offline tests (`VR-012:163-201,263-270`):

- Add the referenced `--max-wer` option to the parser/help, reject non-finite or negative values, and reject non-positive/non-finite timeouts. Reject normalized-empty roundtrip reference text as a usage error.
- Require `--audio` in STT-only mode; reject incompatible mode/options. Specify accepted WAV encodings precisely: raw 8 kHz mono mu-law, WAV mu-law, and little-endian 16-bit PCM WAV at 8 kHz mono; reject other formats. Define PCM-to-mu-law conversion and test known fixture samples without adding a vendor-specific path.
- Make the timeout a monotonic deadline for the entire provider step, including construction/start, blocking synthesis, feeding and settling. On expiry, emit FAIL and exit 1 after bounded cleanup. A timed-out thread/future that continues making provider requests is not cleanup. Prove bounded command exit against blocked synchronous TTS and blocked asynchronous STT fakes.
- Guarantee `stt.stop()` in cleanup after successful start, including feed, callback, timeout and recognition errors. Stop failures are failures, not successful recognition.
- SKIP is for absent local configuration/selected optional dependency only. Authentication rejection, permission/region/quota errors and errors after an attempted configured request are FAIL. In cross-provider roundtrip, preflight both the selected synthesizer and recognizer; identify both in output.
- In `--json` mode, every nonempty stdout line is a JSON object, including metadata; human headers/logs go to stderr. Do not output an unrelated enclosing checkout's SHA as the installed wheel's provenance.

Preserve the documented general-purpose partial-SKIP exit policy, but explicitly distinguish it from live acceptance: an intended provider that skipped was not validated.

### R-6: Make the live and SDK evidence independently reproducible

The SDK evidence is output without its runnable probe, and the two AC-10 commands do not specify how to hold STT open beyond five minutes (`VR-012-research.md:37-47`; `VR-012:271-289`). Commit the exact SDK probe and a Google-only long-session probe, or inline their complete runnable source/commands in committed evidence.

The backend live probe must exercise the implemented `SpeechAsyncClient` path, not substitute the research's synchronous `SpeechClient`. Use numbered/uniquely identified synthetic phrases around the actual rotation boundary; record the expected phrase sequence and resulting commits before/after rotation. Exercise ordinary and forced rotation separately and record any cut-related omissions. A restart log plus some later commit does not establish the stated no-lost-commit gate.

Run exact-floor SDK/message and focused behavior checks on Python 3.11 and 3.12. Record fresh-environment commands for both Google-present and Google-absent suites; installing fewer extras into a previously populated environment does not remove Google. Retain the clean-wheel/build checks and release-checklist lint command. The CLI's later live matrix must contain PASS, not SKIP, for every intended provider/recognizer combination; configure and record Finnish ElevenLabs STT explicitly for its Finnish default-text check.

### R-7: Separate library capability from consumer safety and human authority

Replace "no consumer code changes beyond the provider name and configuration" with an interface-conformance claim only (`VR-012:26-28,302-306`). Acknowledge that callback cadence is not equivalent to Azure and that CSAP rollout remains blocked on its separately judged timer/field acceptance work. Do not add a callback or change consumer timing here.

Carry these explicit owner questions into the successor FRs and record the answers before the affected action:

1. **May the named Google TEST project, Azure subscription and ElevenLabs account be used for the specified billable synthetic-only probes?** Approval for past research is not approval for future runs.
2. **Is the measured forced-rotation in-flight transcription loss acceptable for a library release, while CSAP deployment remains separately gated?** If the accepted loss cannot be bounded and evidenced, leave the backend release blocked; do not authorize overlapping replay/deduplication as an incidental fix.
3. **Does the owner approve the narrowly scoped `publish.yml` extra-install change?** It modifies enforcement infrastructure (`VR-012:144`), so explicit human review is a GATE under the judge doctrine. No permissions, secrets, publishing triggers or other workflow behavior may change.

Tagging and publication remain separate explicit owner decisions. Do not run any live probe or modify CI while preparing these revisions.

## Scope is frozen

The following is the partition for successor review, not permission to implement it under this draft.

| Deliverable | Surface |
|---|---|
| D-1 | Two committed successor FRs, VR-012's disposition/cross-links, and the cited sanitized research/probe evidence. |
| D-2: backend successor only | `voice_runtime/providers/google_stt.py`, `google_tts.py`, the existing `stt.py`/`tts.py` factories, focused provider/interface tests, optional Google dependency metadata, direct synthetic probes. |
| D-3: CLI successor only | `voice_runtime/speech_check.py`, focused offline CLI tests, packaged help and diagnostic documentation. No dependency on an unreleased backend for its offline acceptance. |
| D-4: per successor | Direct README/`.env.example`/changelog/version updates, exact-floor and wheel evidence, and implementation records. Next minor release is selected from the actual enforcement baseline. |
| D-5: conditional backend surface | Only the Google-extra installation change in `.github/workflows/publish.yml`, subject to explicit human review. |

Not authorized: production implementation by this draft; CSAP or black-box repository edits, deployments, calls or credential access; Terraform/IAM/policy changes; automatic failover; language identification; new provider/session/callback contracts; synthetic periodic recognizing events; changes to existing providers/transports; non-streaming TTS fallback or speculative container parsing; replay/deduplication machinery; a generic orchestration framework; unrelated CI/hooks/doctrine changes; real caller/patient audio; tags or publication.

## Revised acceptance criteria

These are requirements for the successor FRs to carry into independent judgement, not a combined implementation checklist.

- [ ] AC-01: Two independently executable FRs contain the R-1 partition, cross-references, relevant evidence and separate acceptance/release gates. The provider FR does not require the packaged CLI.
- [ ] AC-02: Both plans require assertion-based behavioral RED before GREEN, collected tests with the owning requirement marker, fake network clients and no live requests in offline tests. SDK-dependent tests have the explicit R-2 no-extra policy.
- [ ] AC-03: Backend tests retain the original config, adaptation, endpoint, environment, frame, callback and error assertions (original AC-02/03/04/06), plus R-3's complete retry/reset and lag-reset cases.
- [ ] AC-04: Backend rotation tests prove ordinary rotation after 270 seconds, forced rotation at 290 seconds without responses, bounded old-stream drain, serialized transition ownership, partial-batch/queue retention, paced resumption, EOF handling and no resurrection after stop.
- [ ] AC-05: Backend TTS tests prove raw-byte preservation and normal final padding, compatible result dictionaries, optional stop event, bounded stalled-read cancellation, no post-interruption mark/audio, explicit API errors and the actual mark timeout/disconnect behavior.
- [ ] AC-06: Backend factory/protocol checks pass; in a fresh environment without Google SDKs, core and existing provider imports/construction still work, and non-Google tests remain active.
- [ ] AC-07: CLI tests cover mode/option validation, supported/rejected audio inputs, known PCM conversion, capture through the real loop-less session hook, normalized WER and empty-reference handling, both roundtrip provider identities, partial/all SKIP, post-request FAIL and exits 0/1/2.
- [ ] AC-08: Blocked TTS/STT fake processes demonstrate the advertised whole-step timeout and bounded cleanup. STT cleanup runs on every exit after start. Every stdout line in JSON mode parses as a JSON object.
- [ ] AC-09: Runnable committed SDK and long-session probes accompany the plans. Exact advertised SDK floors pass on Python 3.11/3.12; fresh full-suite environments with/without Google, lint, build, `twine check` and clean non-editable wheel checks are specified and recorded by the owning implementation.
- [ ] AC-10: After explicit owner approval, backend live evidence uses the real async provider, synthetic phrases across ordinary and forced rotation, exact versions/code identity and expected-versus-observed commits. Any unaccepted omission, permission/region error or failed intended provider blocks its applicable release gate.
- [ ] AC-11: The CLI live acceptance, independently approved, records every intended Azure/ElevenLabs/Google combination as PASS without counting SKIPs; Finnish ElevenLabs STT configuration is explicit. Google rows apply only after the backend exists.
- [ ] AC-12: Documentation distinguishes interface conformance from consumer timing equivalence, states rotation loss/latency limitations, and keeps CSAP rollout outside library authority. Each successor records decisions, RED/GREEN commits, evidence, deviations and the actual next-minor version.

## Conditions for enforcement

| # | Condition | Severity |
|---|---|---|
| C-1 | Human-review this DRAFT. Neither the draft nor folding revisions alone grants implementation authority. | GATE |
| C-2 | Split the concerns, fold R-1 through R-7 into their owning committed FRs, and obtain an independent judgement for each before production edits. | GATE |
| C-3 | No live service requests or account/credential use without recorded owner approval for that specific probe scope. | GATE |
| C-4 | Human-review the exact CI diff before applying the extra-install change; no other enforcement-infrastructure changes are permitted. | GATE |
| C-5 | Resolve the explicit rotation-loss release decision with measured evidence. Keep CSAP consumer safety/field acceptance separately blocked. | GATE |
| C-6 | Preserve public protocols, existing provider behavior, optional-dependency isolation and behavioral RED/GREEN evidence in each authorized implementation. | GATE |
| C-7 | No tag, publish, deployment or cross-repository change follows from this judgement. Publication requires the owner's separate explicit instruction. | GATE |

Authority granted: none by this advisory SPLIT draft; the Google backend and cross-provider CLI require separate human-reviewed judgements before implementation.
