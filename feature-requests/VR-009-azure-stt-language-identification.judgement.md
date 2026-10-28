# Judgement: VR-009 Azure STT continuous language identification

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
