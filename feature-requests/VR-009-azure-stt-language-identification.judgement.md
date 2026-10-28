# Judgement: VR-009 Azure STT continuous language identification

**Verdict:** APPROVED WITH REVISIONS — the provider-boundary design is clear, testable, and supported by committed research; authority activates only after R-1 through R-3 are folded into the FR.

**Reviewed against:** `/tmp/vr009-wt/feature-requests/VR-009-azure-stt-language-identification.md`; cited research `/tmp/vr009-wt/feature-requests/evidence/VR-009-research.md`; prior draft `/tmp/vr009-wt/feature-requests/VR-009-azure-stt-language-identification.judgement.md`; cited prior art `/tmp/vr009-wt/feature-requests/NC-161-azure-speech-provider.md`, `/tmp/vr009-wt/feature-requests/NC-258-stt-death-detection-recovery.md`, and `/tmp/vr009-wt/feature-requests/VR-005-stt-session-task-ownership.md`; cited implementation surfaces `/tmp/vr009-wt/voice_runtime/providers/azure_stt.py`, `/tmp/vr009-wt/voice_runtime/transports/twilio_ws.py`, and `/tmp/vr009-wt/tests/test_azure_stt.py`; package and operational surfaces `/tmp/vr009-wt/pyproject.toml`, `/tmp/vr009-wt/README.md`, `/tmp/vr009-wt/.env.example`, `/tmp/vr009-wt/.github/workflows/publish.yml`, and `/tmp/vr009-wt/reference/release-checklist.md`; judge doctrine `/Users/sami.j.p.heikkinen/src/yamlgraph/.github/skills/judge-fr/doctrine.md`; judgement template `/Users/sami.j.p.heikkinen/src/yamlgraph/.github/skills/judge-fr/judgement.template.md`; repo doctrine `/Users/sami.j.p.heikkinen/src/yamlgraph/.github/copilot-instructions.md` and `/Users/sami.j.p.heikkinen/src/yamlgraph/CLAUDE.md`. The FR and its research record were verified as tracked at commit `ea085f1`; no uncommitted target-repository artifact was consumed.

## Human promotion record — 2026-10-28

Round 2 rendered by the YAMLGraph judge adapter (`scripts/judge.sh`, backend
copilot, model `gpt-5.6-sol`, session `19ac5b70-15b0-4b7b-a8f5-291fc3b247a5`)
after round 1 (REJECTED, session `d0c0fdab`, kept below) was folded. The owner
instructed to judge and land VR-009 on voice_runtime main. Round-2 R-1 to R-3
were folded into the FR as AC-11 (SDK floor proof at 1.38.0), the minor-release
target, and AC-12 (README and `.env.example`). R-4 of round 1: the owner
approved the AC-08 live probe with synthetic clips only.

## What is sound

The problem is real and the boundary is correct. The current provider stores one locale and repeats the same single-locale recognizer construction in `start()` and `_reconnect()` (`azure_stt.py:53-58,98-113,241-280`), while the transport calls `session.stt_factory()` without arguments (`twilio_ws.py:245`). An opt-in provider-owned setting and one shared recognizer builder are therefore smaller and safer than transport or consumer rewiring.

The revised configuration contract is unusually precise. It defines environment-versus-constructor precedence, explicit-empty behavior, normalization, ordering, syntax rejection, duplicate-base rejection, and the ten-candidate limit (`VR-009:41-53`). The three execution modes are mutually exclusive, and the continuous path specifies the endpoint, property, candidate object, and absence of `speech_recognition_language` (`VR-009:54-64`). Reconnect parity is directly asserted rather than inferred (`VR-009:83-89`).

The observability contract is bounded and privacy-aware. Detected language remains provider-local instead of changing the public callback, the latency value is explicitly a state-machine signal rather than phrase identity, stale timestamps are cleared on every terminal path, and transcript text is excluded from the new log (`VR-009:65-72,90-96`). The live probe uses approved synthetic audio and treats endpoint, region, or detection failure as release-blocking evidence rather than a success-shaped fallback (`VR-009:102-108`).

The research gate is now satisfied in substance. The committed record preserves seven provider constraints, an SDK probe, six genuine solution classes, prior-art dispositions, disagreement about at-start versus continuous LID, and an explicit non-graph conclusion (`VR-009-research.md:11-23,27-52,54-72`). The proposal is one concern: an opt-in Azure provider configuration extension with the minimum observability needed to evaluate it. Strategic classification: **contrib/provider extension** — one named consumer exercises a gap in an existing provider abstraction; this is not a framework primitive.

Acceptance is mostly mechanical and supports behavioral RED: parser cases, initial and reconnect construction, result logging, timestamp clearing, import-time environment parsing, a live synthetic probe, package artifacts, and unchanged callback/echo behavior all admit direct assertions (`VR-009:76-110`). The out-of-scope list correctly excludes consumer language policy, TTS, Sámi, unsupported `sv-FI`, and ElevenLabs changes (`VR-009:124-129`).

## Required revisions

### R-1: Close the supported Azure SDK floor

The evidence proves the required API only on `azure-cognitiveservices-speech==1.51.0` (`VR-009:34`; `VR-009-research.md:27-50`), while the package advertises `>=1.38.0` (`pyproject.toml:29`). Before enforcement, run and record the SDK-surface and focused behavior tests against exactly `1.38.0`. If every required symbol, constructor form, property, result wrapper, and mocked construction test passes, retain the floor and add that exact-floor command to AC-09. If any required surface fails, raise the `azure` extra floor to the lowest version demonstrated by the same probe and run AC-02 through AC-07 plus the clean-wheel check against that exact version. Do not claim support from a latest-version-only install.

### R-2: Use the repository's feature-release version rule

Replace the patch target `0.1.16` (`VR-009:8`) with “the next minor release from the actual release baseline at enforcement.” The repository defines new features as MINOR bumps (`reference/release-checklist.md:69-74`), while the current package is `0.1.15` (`pyproject.toml:7`). Update AC-09 and the CSAP NC-566 coordination note so the consumer pins the version actually selected by that rule. Tagging and publication remain behind the owner's separate explicit decision.

### R-3: Add the public configuration surfaces

Add the directly related documentation to the frozen scope and acceptance criteria: document `languages=` and `AZURE_STT_LANGUAGES` in the Azure STT section of `README.md`, add the variable to the README environment table, and add a commented example to `.env.example`. State the comma-delimited locale form, one-locale single-mode behavior, two-or-more continuous-mode behavior, ten-candidate and unique-base limits, import-time failure for invalid environment values, the closed-set nature of Azure detection, and that unset preserves `language_code`. The repository currently documents the existing Azure STT constructor and Azure environment variables at `README.md:370-379,410-423` and `.env.example:26-30`; shipping an operational environment variable without those surfaces would leave the first consumer as private knowledge.

## Scope is frozen

| Deliverable | Surface |
|---|---|
| D-1 | Revised `feature-requests/VR-009-azure-stt-language-identification.md` folding R-1 through R-3 |
| D-2 | `voice_runtime/providers/azure_stt.py`: parser, mode selection, shared recognizer construction, detected-language logging, and commit-lag state |
| D-3 | `tests/test_vr009_azure_stt_language_id.py` plus only directly necessary updates to existing Azure provider tests |
| D-4 | `pyproject.toml` only if the exact-floor probe requires a higher Azure SDK minimum; version metadata uses the next-minor rule |
| D-5 | `README.md`, `.env.example`, and `CHANGELOG.md` for the new opt-in configuration and release |
| D-6 | Synthetic live-probe script/output recorded in the FR, package artifacts, and clean-wheel verification |

Not authorized: CSAP code, configuration, deployment, pins, calls, logs, or field acceptance; callback-signature changes; reply-language, prompt, voice, predefined-audio, or TTS policy; ElevenLabs changes; support claims for untested Azure SDK versions; unrelated provider refactors; CI, hook, judge, review, or other enforcement-infrastructure changes; graph or prompt artifacts; real caller or patient audio; tag creation, tag push, TestPyPI upload, or PyPI publication without the owner's explicit release instruction.

## Revised acceptance criteria

- [ ] AC-01: The FR folds R-1 through R-3, records the exact Azure SDK floor decision and evidence, uses the next-minor release rule, and names the documentation surfaces.
- [ ] AC-02: A test-only behavioral RED commit adds `tests/test_vr009_azure_stt_language_id.py`; failures are assertions against current behavior, not import, collection, dependency, or fixture errors. Every new or changed behavioral test carries `@pytest.mark.req("VR-009")`.
- [ ] AC-03: `_parse_languages` implements the frozen normalization contract for `None`, strings, tuples, lists, whitespace, empty items, explicit empty sequences, invalid types, invalid syntax, duplicate base languages, and more than ten candidates.
- [ ] AC-04: With the environment unset and no explicit languages, initial construction and reconnect retain region-based `SpeechConfig`, `fi-FI`, timeout, callbacks, and absence of auto-detect configuration. Existing focused tests pass without weakened assertions.
- [ ] AC-05: With `("fi-FI", "sv-SE", "en-GB")`, initial construction and reconnect both use the exact regional universal-v2 endpoint, continuous LID property, ordered `AutoDetectSourceLanguageConfig`, no assigned `speech_recognition_language`, and otherwise unchanged stream/callback configuration.
- [ ] AC-06: Committed-result observability proves detected versus configured `lang`, stripped `chars`, no transcript in the new log, exact monotonic integer conversion, `-1` without an interim event, no stale timing across commits or discarded results, and no callback/new committed log on existing echo-discard paths.
- [ ] AC-07: Module reload under a valid environment yields LID mode; invalid environment input raises `ValueError` at import; explicit constructor sequences obey the frozen precedence rules, including explicit empty and one-locale sequences.
- [ ] AC-08: The owner-approved live probe uses only synthetic, non-personal 8 kHz mu-law Finnish and Swedish clips and records region, exact package/SDK versions, clip provenance, detected language, commit lag, synthetic text, and a single-locale Finnish baseline. Endpoint cancellation, unsupported-region behavior, or failure to identify both clip languages blocks release.
- [ ] AC-09: The required Azure SDK surfaces and focused tests pass at the exact advertised minimum version per R-1. The full offline suite passes; the next-minor version, changelog, README, and `.env.example` are updated; sdist and wheel build; `twine check` passes; and a clean non-editable wheel install with the `azure` extra exercises both unset and valid `AZURE_STT_LANGUAGES` paths at that minimum version.
- [ ] AC-10: The FR records implementation decisions, behavioral RED/GREEN commits, exact-floor evidence, focused and full-suite results, artifact checks, live-probe output, and deviations. Consumer wiring and field acceptance remain assigned to CSAP NC-566; no tag or publication occurs without explicit owner instruction.

## Conditions for enforcement

| # | Condition | Severity |
|---|---|---|
| C-1 | Fold R-1 through R-3 into the committed FR before implementation authority activates. | GATE |
| C-2 | Produce behavior-caused RED before GREEN and preserve single-locale defaults, reconnect behavior, callback signatures, echo discard, and task ownership. | GATE |
| C-3 | The advertised Azure SDK minimum must be the exact version exercised by the required surface and focused behavior checks. | GATE |
| C-4 | Use the next-minor version rule; do not publish this feature as an undocumented patch-version exception. | GATE |
| C-5 | Submit only synthetic, non-personal audio to Azure; endpoint, region, or language-identification failure blocks release. | GATE |
| C-6 | Tagging and publication remain separate human decisions after implementation review and artifact inspection. | GATE |

Authority granted: after R-1 through R-3 are committed, implement the frozen Azure STT opt-in language-identification extension, its provider-local observability, direct tests, documentation, package metadata, and approved synthetic probe only.

---

## Round 1 (REJECTED, superseded)

**Verdict:** REJECTED — the provider-boundary direction is plausible, but the mandatory research evidence is not a committed artifact and the input-normalization and latency contracts are not yet precise enough to grant implementation authority; revise or re-file, commit the evidence, and obtain a fresh judgement.

**Reviewed against:** `/tmp/vr009-wt/feature-requests/VR-009-azure-stt-language-identification.md`; cited `/tmp/vr009-wt/voice_runtime/providers/azure_stt.py`; cited `/tmp/vr009-wt/voice_runtime/transports/twilio_ws.py`; cited `/tmp/vr009-wt/tests/test_azure_stt.py`; cited `/tmp/vr009-wt/feature-requests/NC-161-azure-speech-provider.md`; cited `/tmp/vr009-wt/feature-requests/NC-258-stt-death-detection-recovery.md`; cited `/tmp/vr009-wt/feature-requests/VR-005-stt-session-task-ownership.md`; precedent `/tmp/vr009-wt/feature-requests/VR-007-media-teardown-region-hangup.judgement.md`; `/tmp/vr009-wt/pyproject.toml`; `/tmp/vr009-wt/PUBLISHING.md`; `/tmp/vr009-wt/reference/release-checklist.md`; committed cited consumer seams `/Users/sami.j.p.heikkinen/src/customer-service-agent-platform/server_fsm.py`, `/Users/sami.j.p.heikkinen/src/customer-service-agent-platform/services/persistent_stt.py`, and `/Users/sami.j.p.heikkinen/src/customer-service-agent-platform/infra/kustomize/overlays/gcp_test/csap.test.environment`; judge doctrine `.github/skills/judge-fr/doctrine.md`; judgement template `.github/skills/judge-fr/judgement.template.md`; repo doctrine `.github/copilot-instructions.md` and `CLAUDE.md`. The cited CSAP research file `/Users/sami.j.p.heikkinen/src/customer-service-agent-platform/docs/2026-10-28-i18n-vs-finnish-bindings-research.md` was found untracked and its contents were not consumed.

## What is sound

The problem is real and located at the provider boundary. `AzurePersistentStt` stores one `language_code`, applies it in both initial construction and reconnect, and logs that single locale (`azure_stt.py:49-59,98-122,241-281`). The transport invokes `session.stt_factory()` without arguments (`twilio_ws.py:243-248`), so an opt-in provider-owned environment setting is smaller than changing transport wiring. The FR preserves unconfigured single-locale behavior and excludes consumer policy, TTS, ElevenLabs, and callback-signature changes (`VR-009:15-19,50-60,72,114-119`).

The proposed recognizer-builder extraction aligns with the existing reconnect responsibility. Initial construction and `_reconnect()` currently duplicate stream, `SpeechConfig`, locale, timeout, recognizer, and callback setup (`azure_stt.py:83-116,241-281`). One private builder is the minimal way to prevent reconnect from silently losing LID configuration, and AC-05 correctly requires assertions against the second recognizer's actual construction arguments rather than existence alone (`VR-009:53-60,83-88`).

The acceptance surface is largely mechanical. Baseline construction, endpoint construction, continuous-mode property assignment, candidate order, reconnect parity, callback preservation, echo discard, environment reload, package build, and clean-wheel behavior all admit direct tests (`VR-009:76-100`). The existing Azure tests already expose suitable mocked SDK seams for recognizer construction and transcript callbacks (`test_azure_stt.py:28-108,143-218`).

The proposal has one responsibility: opt-in Azure STT language identification plus the minimum provider-local observability needed to evaluate it. Reply-language selection, voice selection, prompts, deployment, and consumer field acceptance remain outside the library change (`VR-009:9,35-38,100,114-119`). Strategic classification: **contrib/provider configuration extension** — one named consumer needs a gap filled in an existing provider abstraction; this is not a framework primitive or pattern-only documentation (`VR-009:9-10,102-112`).

The strongest case against rejection is that the FR already contains a useful alternatives table and an explicit graph-fit answer (`VR-009:102-112`). That substance should be preserved. It cannot, however, replace the hard input-closure gate: the metadata has no `**Research:**` pointer (`VR-009:3-11`), and the specifically cited CSAP research file is uncommitted, so the judge cannot inspect or rely on it. Repo doctrine requires a committed, substantive research record before a newly created FR receives authority.

## Required revisions

### R-1: Commit and point to the complete research record

Add a `**Research:**` metadata field pointing to a committed evidence artifact. Commit the cited CSAP research record or promote an equivalent record into the voice-runtime repository. The record must preserve 4-6 genuine solution classes, disposition NC-161, NC-258, VR-005, at-start LID, continuous LID, consumer-side parsing, and the ElevenLabs alternative, and retain the explicit `Is this a graph? No` conclusion. Include the Microsoft Learn URL, fetch date, exact quoted constraints relied upon, SDK version and probe command/output, and a sanitized pointer for the HP-41 incident claim. Do not include patient, caller, credential, or other sensitive content.

### R-2: Define one exact input-normalization contract

Replace the ambiguous `_parse_languages(raw)` contract with:

`_parse_languages(raw: str | Sequence[str] | None) -> tuple[str, ...]`.

Define that `None` returns `()`; strings are comma-split; non-string sequences are consumed item by item; every item must be a string, is stripped, and empty items are dropped; order is preserved; invalid item types, invalid locale syntax, duplicate base languages, and more than 10 nonempty entries raise `ValueError`. State that module import computes one parsed `AZURE_STT_LANGUAGES` tuple, that `languages=None` uses that tuple, and that an explicit sequence is separately normalized by the same function. Because parsing occurs at import, an invalid environment value must fail import even when a later constructor call would supply an explicit sequence. Add tests for tuple/list input, whitespace and empty sequence items, a non-string item, an explicit empty sequence, and precedence between the parsed module value and an explicit sequence (`VR-009:42-49,77-81,90-92`).

### R-3: Make the latency signal truthful and stateful

Remove the unsupported claim that the callback pair identifies "the same phrase" (`VR-009:68`). Define `commit_lag_ms` as elapsed monotonic time from the most recent eligible nonempty `recognizing` event since the previous committed or discarded result. `_on_committed` must consume and clear that timestamp before forwarding or returning; a commit with no eligible interim event logs `-1`; speaking and echo-discard outcomes must clear it so a later phrase cannot inherit stale timing. Add direct tests for no-interim, interim-then-commit, two consecutive commits, speaking discard, echo-window discard, and monotonic integer conversion. Keep transcript text out of the new committed log.

### R-4: Separate the live provider decision from implementation authority

Record this explicit owner question in the FR: **May enforcement use the configured Azure subscription for the AC-08 billable live probe with only synthetic, non-personal Finnish and Swedish clips?** Until the owner answers affirmatively, implementation authority must cover offline tests and local artifact preparation only. If approved, record the region, SDK/package version, clip provenance, sanitized language/latency output, and single-locale baseline. A v2 endpoint cancellation is evidence that blocks release; it is not successful acceptance. Keep tag creation, tag push, and publication behind the separate explicit owner release decision already stated in AC-09 (`VR-009:93-100`; `reference/release-checklist.md:3-39`).

## Scope is frozen

| Deliverable | Surface |
|---|---|
| D-1 | A revised or newly filed `VR-009` planning document containing R-1 through R-4 |
| D-2 | A committed, sanitized research/evidence artifact referenced by the FR metadata |
| D-3 | A fresh judgement artifact rendered only after D-1 and D-2 are committed |

Not authorized: edits to `voice_runtime/providers/azure_stt.py`, tests, README, environment-variable documentation, version metadata, or changelog; Azure API calls; use of credentials; live audio submission; CSAP code, configuration, deployment, pins, calls, logs, or field acceptance; callback-signature changes; reply-language or TTS policy; ElevenLabs changes; tag creation or push; TestPyPI/PyPI publication; graph or prompt artifacts; or judge/review/enforcement infrastructure.

## Revised acceptance criteria

- [ ] AC-01: The FR metadata points to a committed substantive research record satisfying R-1; the cited evidence is committed and available inside judge input closure.
- [ ] AC-02: A test-only behavioral RED commit adds `tests/test_vr009_azure_stt_language_id.py`; failures are assertions against current behavior, not import, collection, dependency, or fixture errors. Every new or changed behavioral test carries `@pytest.mark.req("VR-009")`.
- [ ] AC-03: `_parse_languages` implements R-2 exactly for `None`, comma-delimited strings, tuples, and lists; preserves normalized order; rejects invalid types, syntax, duplicate bases, and 11 candidates; and defines explicit empty-sequence behavior.
- [ ] AC-04: With the environment unset and no explicit languages, initial construction and reconnect retain the current region-based `SpeechConfig`, `fi-FI`, timeout, callbacks, and absence of auto-detect configuration. Existing focused tests pass without weakened assertions.
- [ ] AC-05: With `("fi-FI", "sv-SE", "en-GB")`, initial construction and reconnect both use the exact regional universal-v2 endpoint, continuous LID property, ordered `AutoDetectSourceLanguageConfig`, no assigned `speech_recognition_language`, and otherwise unchanged stream/callback configuration.
- [ ] AC-06: Committed-result observability implements R-3 exactly. Tests prove detected versus configured `lang`, stripped `chars`, no transcript in the new log, `-1` without an interim event, no stale timing across commits or discarded results, and no callback/log on existing echo-discard paths.
- [ ] AC-07: Module reload under a valid environment yields LID mode; reload under an invalid environment raises `ValueError`; explicit constructor sequences follow the precedence and normalization rules frozen by R-2.
- [ ] AC-08: Only after explicit owner approval, a synthetic 8 kHz mu-law Finnish/Swedish live probe records sanitized region, package version, detected locale, latency signal, and single-locale Finnish baseline. Endpoint cancellation or unsupported-region behavior blocks release and is recorded as failure evidence.
- [ ] AC-09: The full offline suite passes; version/changelog/documentation are updated; sdist and wheel build; `twine check` passes; and a clean non-editable wheel installation exercises both unset and valid `AZURE_STT_LANGUAGES` paths. No tag or upload is performed without explicit owner approval.
- [ ] AC-10: The FR records implementation decisions, behavioral RED/GREEN commits, focused and full-suite results, artifact checks, approved probe output if run, and deviations. Consumer wiring and field acceptance remain assigned to CSAP NC-566.

## Conditions for enforcement

| # | Condition | Severity |
|---|---|---|
| C-1 | Commit the research evidence and fold R-1 through R-4 into a revised or new FR before requesting a fresh independent judgement. | GATE |
| C-2 | This rejected draft grants no production, test, documentation, release, credential, or live-provider implementation authority. | GATE |
| C-3 | A future enforcer must produce behavior-caused RED before GREEN and preserve single-locale defaults, reconnect behavior, callback signatures, echo discard, and task ownership. | GATE |
| C-4 | No live Azure request may occur without the explicit owner approval stated in R-4; only synthetic, non-personal audio may be submitted. | GATE |
| C-5 | Endpoint cancellation, unsupported-region behavior, or inability to identify the two synthetic language clips blocks release rather than being converted into a success-shaped fallback. | GATE |
| C-6 | Tagging and publication remain separate human decisions after implementation review and artifact inspection. | GATE |

Authority granted: none; only planning and committed-evidence repair may proceed before a fresh judgement.
