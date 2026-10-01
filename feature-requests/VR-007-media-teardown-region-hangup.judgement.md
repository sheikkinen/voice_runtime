# Judgement: VR-007 Region-aware REST hangup at media teardown

**Verdict:** APPROVED WITH REVISIONS — the transport-boundary fix is evidenced, minimal, and directly testable, but authority activates only after the FR folds the mandatory research pointer and graph-fit disposition, replaces proxy acceptance with explicit ambient-mode and validator witnesses, and reserves publication for explicit human approval.

**Reviewed against:** `projects/voice_runtime/feature-requests/VR-007-media-teardown-region-hangup.md`; cited `projects/voice_runtime/feature-requests/VR-002-twilio-http-timeout.md`; cited `projects/voice_runtime/feature-requests/VR-003-rest-first-call-end-31921.md` and `projects/voice_runtime/feature-requests/VR-003-rest-first-call-end-31921.judgement.md`; cited `projects/voice_runtime/feature-requests/VR-004-region-aware-hangup.md`; cited `projects/voice_runtime/feature-requests/VR-006-region-aware-cdr-read.md` and `projects/voice_runtime/feature-requests/VR-006-region-aware-cdr-read.judgement.md`; cited `projects/voice_runtime/voice_runtime/transports/twilio_ws.py`, `projects/voice_runtime/voice_runtime/transports/twilio_call.py`, `projects/voice_runtime/voice_runtime/transports/_twilio_models.py`, `projects/voice_runtime/voice_runtime/transports/_twilio_client.py`, `projects/voice_runtime/tests/test_vr003_rest_first_call_end_31921.py`, `projects/voice_runtime/tests/test_vr004_region_hangup.py`, `projects/voice_runtime/pyproject.toml`, and `projects/voice_runtime/PUBLISHING.md`; cited sibling-repository artifacts `/Users/sami.j.p.heikkinen/src/customer-service-agent-platform/feature-requests/evidence/NC-488-reconciler-diagnosis.md`, `/Users/sami.j.p.heikkinen/src/customer-service-agent-platform/feature-requests/NC-493-region-aware-reaper-hangup.md`, `/Users/sami.j.p.heikkinen/src/customer-service-agent-platform/services/supervisor_reaper.py`, and `/Users/sami.j.p.heikkinen/src/customer-service-agent-platform/server_fsm.py`; judge doctrine `.github/skills/judge-fr/doctrine.md`; judgement template `.github/skills/judge-fr/judgement.template.md`; repo doctrine `.github/copilot-instructions.md` and `CLAUDE.md`.

## Human promotion record — 2026-10-01

Rendered by the YAMLGraph judge adapter (`scripts/judge.sh`, backend copilot,
model `gpt-5.6-sol`, session `d7d8d342-b18f-4668-8c21-ba610aa821a4`). After the
draft was presented, the owner instructed: **"fold. commit push voice_runtime -
probably no PR needed. enforce"**. R-1 through R-4 were folded into the FR in
the same docs commit as this file. That instruction is the human promotion
required by C-1; it does not answer R-4's separate release question.

## What is sound

The problem is real and the proposed scope is smaller than inventing another REST path. The current media handler requires ambient account credentials before it attempts REST and invokes `hangup_call(call_sid)` without regions (`twilio_ws.py:135-151`). The existing provider callable already owns explicit regional routing, validates the region tuple, and distinguishes regional absence from terminal success (`twilio_call.py:123-169`). VR-007 connects those two existing abstractions through one keyword-only registration input rather than duplicating Twilio client or routing logic (`VR-007:28-40`).

The cited consumer seam exists. CSAP already constructs the ordered US1/IE1 `TwilioRegion` tuple in `services/supervisor_reaper.py:24-61`, while its media route still registers `register_voice_websocket(app, session)` without that tuple (`server_fsm.py:232`). The FR correctly excludes this cross-repository wiring from library authority and assigns it to a separate consumer FR (`VR-007:79-92`). NC-488 establishes the broader rejected-credential and regional-leg context, and VR-007 honestly records that its newer local probe did not test the deployed TEST secret or the media-WebSocket path (`VR-007:22,55-68`).

The design aligns with established architecture. VR-002 keeps Twilio requests behind the bounded `_twilio_client.py` construction boundary; VR-003 already offloads `hangup_call` with `asyncio.to_thread` and performs a bounded wait before server-side close (`twilio_ws.py:150-178`); VR-004 owns the explicit-region sweep and expressly left API-key-only media teardown unclaimed (`VR-004:84-87`). Passing the already typed `TwilioRegion` tuple into the existing teardown closure therefore fixes the call site, preserves the provider boundary, and adds no dependency or environment-name parsing.

The proposal has one responsibility: select the already implemented explicit-region hangup mode when a consumer supplies regional configuration at media-route registration. Moving the existing tuple checks into one private helper is inseparable from that responsibility because registration-time rejection requires the same validation contract that `hangup_call` currently applies at call time (`twilio_call.py:132-143`; `VR-007:35`). CDR reads, reaper policy, consumer key discovery, region ordering, deployment, and field acceptance remain outside the library change (`VR-007:79-92`).

The behavioral core is feasible and testable without network access. Existing VR-003 tests already drive the captured FastAPI WebSocket endpoint and fake Twilio-side disconnects, while VR-004 tests already exercise exact error classes, tuple revalidation, duplicate hosts, timeout propagation, and secret-safe diagnostics (`test_vr003_rest_first_call_end_31921.py:121-333`; `test_vr004_region_hangup.py:65-261`). VR-007 can extend those proven seams rather than mock its nested `rest_hangup_first` closure.

Strategic classification: **contrib/provider transport-boundary defect fix**. It has one named consuming application and closes a specific gap between two existing voice-runtime abstractions; it is neither a YAMLGraph framework primitive nor pattern-only documentation (`VR-007:9-10,20-35`; `judge-fr/doctrine.md:51-57`).

The strongest case against approval is that the FR currently fails two governance boundaries and one acceptance-quality boundary, not that the implementation direction is wrong. The metadata has no mandatory `**Research:**` pointer, the otherwise substantive alternatives record omits the required `is_this_a_graph` answer (`VR-007:9-10,46-68`; `judge-fr/doctrine.md:118-130`), AC-04 treats unchanged test passage as proof of ambient semantics rather than asserting those semantics (`VR-007:75`), and AC-06 absorbs PyPI publication even though the release procedure requires credentials and agreement by three parties (`VR-007:77`; `PUBLISHING.md:8,107-131`). These defects are mechanically foldable.

## Required revisions

### R-1: Satisfy the prospective research gate in the FR

Add `**Research:** [Dispositioned alternatives](#research-and-prior-art)` to the metadata. Add an explicit `**Is this a graph?** No: this is deterministic provider transport and WebSocket teardown with no LLM stage.` statement to the research section. Extend the prior-art table or an adjacent table to disposition every precedent named in the metadata: VR-002 supplies the per-request timeout and single client boundary; VR-003 supplies REST-first teardown, off-loop execution, bounded inbound-close waiting, and WS fallback; VR-004 supplies explicit-region routing and its media-teardown carve-out; VR-006 is adjacent regional-provider work but changes CDR reads, not call teardown; NC-488 supplies incident context whose older token diagnosis is narrowed by the new probe; and NC-493 supplies the existing consumer tuple but grants no media-route wiring authority. Preserve the explicit uncertainty that the deployed TEST secret and media-WebSocket path were not probed.

### R-2: Define one validator's data flow and startup witness

State that the private helper accepts only a nonempty tuple, revalidates every element with `TwilioRegion.model_validate`, rejects duplicate hosts, and returns the validated `tuple[TwilioRegion, ...]`. `register_voice_websocket` must call it synchronously and retain its returned tuple before route execution; `hangup_call` must use that same helper and returned tuple. Amend AC-05 to cover an empty tuple, a list, duplicate hosts, a later invalid object, and a `TwilioRegion.model_construct(...)` instance with invalid account/auth data. Each registration case must fail before route use or client construction, and diagnostics must contain no auth value. This makes "fails at startup" and "one validator" directly testable rather than implementation prose.

### R-3: Replace proxy preservation and add requirement traceability

Replace AC-04's claim that unchanged tests prove preservation with direct ambient-mode assertions: with `hangup_regions=None` and missing ambient credentials, no REST call occurs and exactly one server-side close occurs; with ambient credentials, `hangup_call` is called without a `regions` keyword; successful REST followed by Twilio-side disconnect emits no server-side close; and ambient REST failure emits exactly one fallback close. Keep existing VR-003 and VR-004 suites as regression coverage, but do not use their mere passage as the semantic witness. Require every new or changed behavioral test to carry `@pytest.mark.req("VR-007")`, following the existing VR-003/VR-004 project convention.

### R-4: Separate release preparation from the human publication decision

Revise AC-06 so implementation authority covers the `0.1.15` version bump, VR-007 changelog entry, full offline suite, distribution build, `twine check`, and clean non-editable wheel smoke for the changed registration API. Remove TestPyPI/PyPI upload, tag creation, and tag push from automatic acceptance. Record the explicit owner/release-manager question in the FR: **After implementation review and artifact inspection, do the required parties approve publishing and tagging voice-runtime 0.1.15?** Actual upload and tag push require that affirmative human decision; this judgement does not absorb credential use, release-content agreement, or publication authority (`judge-fr/doctrine.md:100-101`; `PUBLISHING.md:8,107-131`).

## Scope is frozen

| Deliverable | Surface |
|---|---|
| D-1 | `projects/voice_runtime/voice_runtime/transports/twilio_ws.py`: keyword-only `hangup_regions`, synchronous registration validation, and explicit-region selection inside the existing REST-first teardown path |
| D-2 | `projects/voice_runtime/voice_runtime/transports/twilio_call.py`: one private region-tuple validator and the minimal `hangup_call` refactor needed to consume its returned tuple without changing routing outcomes |
| D-3 | `projects/voice_runtime/tests/test_vr007_media_teardown_regions.py` plus only minimal focused updates to existing VR-003/VR-004 tests needed for the frozen behavior |
| D-4 | `projects/voice_runtime/feature-requests/VR-007-media-teardown-region-hangup.md`: folded revisions, implementation decisions, status, and verification record |
| D-5 | `projects/voice_runtime/pyproject.toml`, `projects/voice_runtime/CHANGELOG.md`, and local build artifacts/checks needed to prepare and inspect version 0.1.15 |

Not authorized: CSAP code, dependency pins, environment parsing, secret access or mutation, deployment, TEST/STG calls, log queries, field acceptance, reaper or CDR changes; region-order policy; changes to `TwilioRegion`, explicit-client construction, timeout semantics, 404/20404 or 400/21220 classification; signature-validation behavior; retries; a new REST implementation; new dependencies; broad WebSocket refactoring; weakening existing assertions; TestPyPI/PyPI upload; git tag creation or push; graph or prompt artifacts; or judge/review/enforcement infrastructure.

## Revised acceptance criteria

- [ ] AC-01: A test-only behavioral RED commit adds `tests/test_vr007_media_teardown_regions.py`; failures are assertions against current behavior, not import, collection, or fixture errors. Every new or changed behavioral test carries `@pytest.mark.req("VR-007")`.
- [ ] AC-02: The shared private validator returns a fully revalidated nonempty `tuple[TwilioRegion, ...]` with distinct hosts. Registration invokes it synchronously and retains that returned tuple. Empty tuple, list, duplicate hosts, later invalid object, and invalid `model_construct` instance each raise `ValueError` before route use/client construction, with no auth value in the exception.
- [ ] AC-03: With validated `hangup_regions` supplied and both `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` unset, a requested disconnect invokes `hangup_call` exactly once through the existing off-loop worker with `regions=<validated tuple>`. When the fake Twilio transport then disconnects, no server-side `close(1000)` is emitted.
- [ ] AC-04: With validated `hangup_regions` supplied, each of `TwilioCallNotFoundError`, `TwilioRestException(status=401, code=20003)`, and `requests.exceptions.ReadTimeout` from `hangup_call` produces exactly one server-side `close(1000)`, no retry, and no credential material in logs.
- [ ] AC-05: With validated `hangup_regions` supplied and REST success but no Twilio-side disconnect before `REST_CLOSE_WAIT_S`, the existing bounded wait ends in exactly one server-side `close(1000)`.
- [ ] AC-06: With `hangup_regions=None`, missing ambient credentials cause zero REST calls and exactly one server-side close; ambient credentials invoke `hangup_call(call_sid)` without `regions`; REST success followed by Twilio-side disconnect causes no server-side close; and ambient REST failure causes exactly one fallback close.
- [ ] AC-07: `hangup_call` uses the shared validator while retaining VR-004 behavior: complete validation precedes client construction, 404/20404 alone advances the region sweep, 2xx or 400/21220 stops successfully, all-region absence raises `TwilioCallNotFoundError`, and any other provider/transport error propagates without a later attempt. Existing VR-004 focused tests pass without weakened assertions.
- [ ] AC-08: WebSocket upgrade signature validation continues to use `TWILIO_AUTH_TOKEN`; supplying `hangup_regions` neither bypasses nor replaces that separate boundary. No new environment variables or consumer-specific key names are introduced.
- [ ] AC-09: The full offline voice-runtime suite passes. Version metadata is `0.1.15`, `CHANGELOG.md` records VR-007, the sdist and wheel build successfully, `twine check` passes, and a clean non-editable wheel installation imports and exercises the changed registration API. No upload or tag operation is part of this criterion.
- [ ] AC-10: The FR records implementation decisions, RED/GREEN witnesses, focused and full-suite results, artifact inspection, and any deviation. It keeps consumer wiring and field acceptance explicitly assigned to a separate CSAP FR.

## Conditions for enforcement

| # | Condition | Severity |
|---|---|---|
| C-1 | Fold R-1 through R-4 into the FR and obtain human promotion of this advisory draft before implementation authority activates. | GATE |
| C-2 | Produce behavior-caused RED before production GREEN; collection/import failures and live-network failures are not valid RED witnesses. | GATE |
| C-3 | Keep one region-tuple validator in `twilio_call.py`; registration and `hangup_call` must consume its returned validated tuple rather than duplicate or bypass checks. | GATE |
| C-4 | Preserve VR-002/VR-003/VR-004 contracts: bounded provider requests, off-loop REST execution, bounded Twilio-side-close wait, exact regional outcome classification, and WS fallback on every exception. | GATE |
| C-5 | Preserve ambient media teardown and WebSocket signature authentication exactly as witnessed by AC-06 and AC-08; explicit hangup credentials must not replace signature credentials. | GATE |
| C-6 | Never expose auth values through repr, validation errors, warnings, or fallback logs. | GATE |
| C-7 | Do not edit or operate CSAP, credentials, deployments, live calls, or field telemetry under this authority. | GATE |
| C-8 | Prepare and inspect 0.1.15 locally only. TestPyPI/PyPI upload and tag creation/push require a separate explicit affirmative human release decision after review. | GATE |

Authority granted: after the FR folds R-1 through R-4 and a human promotes this draft, implement and locally prepare VR-007's validated consumer-supplied regional REST hangup at the existing media-teardown call site, within the frozen voice-runtime surfaces and without consumer wiring or publication.
