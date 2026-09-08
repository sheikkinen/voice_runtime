# Judgement: VR-006 Explicit-region, destination-filtered, complete CDR reads

**Verdict:** APPROVED — the revised request folds the round-one path-safety and independent-authority requirements, explicitly owns its Pydantic dependency, and defines a cohesive, mechanically testable provider-boundary fix.

**Reviewed against:** `projects/voice_runtime/feature-requests/VR-006-region-aware-cdr-read.md`; prior promoted judgement `projects/voice_runtime/feature-requests/VR-006-region-aware-cdr-read.judgement.md`; cited `projects/voice_runtime/feature-requests/VR-002-twilio-http-timeout.md` and `projects/voice_runtime/feature-requests/VR-002-twilio-http-timeout.judgement.md`; cited `projects/voice_runtime/feature-requests/VR-004-region-aware-hangup.md` and its promoted judgement `projects/voice_runtime/feature-requests/VR-004-region-aware-hangup.judgement.md`; cited `projects/voice_runtime/voice_runtime/transports/twilio_call.py`, `projects/voice_runtime/voice_runtime/transports/_twilio_client.py`, `projects/voice_runtime/tests/test_vr002_twilio_http_timeout.py`, and `projects/voice_runtime/pyproject.toml`; cited sibling-project baseline `customer-service-agent-platform@7f0442bc8ccf2bbd54f76f691937b34ee88adc94`, specifically `customer-service-agent-platform/feature-requests/NC-492-reconciler-restoration.md`, `customer-service-agent-platform/feature-requests/NC-493-region-aware-reaper-hangup.md`, `customer-service-agent-platform/feature-requests/evidence/NC-488-reconciler-diagnosis.md`, `customer-service-agent-platform/troubleshooting/gather_twilio.py`, and `customer-service-agent-platform/feature-requests/VBOT-97-twilio-sms-standalone-component.md`; judge doctrine `.github/skills/judge-fr/doctrine.md`; judgement template `.github/skills/judge-fr/judgement.template.md`; repo doctrine `.github/copilot-instructions.md`.

## What is sound

The problem is real and the scope is minimal. The current `list_recent_calls` reads ambient credentials and destination, calls `calls.list(..., limit=200)`, and returns only three untyped values (`twilio_call.py:150-175`). The cited incident record shows that the deployed read used rejected credentials, sent `To=None`, queried the wrong region, and missed inbound legs that existed only in IE1 (`NC-488-reconciler-diagnosis.md:13-25,51-76`). A separate explicit callable corrects that provider contract without silently changing deployed callers; the old callable is preserved apart from a deprecation docstring (`VR-006:106-110`).

The round-one defects are folded. Account identity is now a strict ASCII alphanumeric path segment with explicit rejection classes before client construction (`VR-006:41-49,144-149`), satisfying the prior R-1 (`VR-006-region-aware-cdr-read.judgement.md:23-25`). VR-006 also isolates its callable, exports, tests, smoke, and release authority from VR-004's hangup work (`VR-006:166-174,178-184`), satisfying prior R-2 (`VR-006-region-aware-cdr-read.judgement.md:27-29`).

The dependency re-plan is honest and feasible. The manifest currently declares FastAPI, Uvicorn, Twilio, and HTTPX but not Pydantic (`pyproject.toml:16-21`). The FR now explicitly requires `pydantic>=2.0,<3` because `TwilioCallRow` is a public Pydantic contract, limits this to one direct runtime declaration shared with VR-004, and proves the built distribution in a clean non-editable environment (`VR-006:85-98,166-174,178-190`). This resolves rather than conceals the dependency gap that caused the previous authority to exclude new dependencies.

The design aligns with established architecture. VR-002 made `_twilio_client.py` the sole lazy, bounded Twilio construction boundary (`_twilio_client.py:1-6,16-26`; `VR-002-twilio-http-timeout.judgement.md:49-65`). The cited collector demonstrates separate auth and account-path identity, explicit regional hosts, bounded pagination, defensive time filtering, and preservation of continuation query state (`gather_twilio.py:113-150,213-225`). VR-006 reuses those semantics while adding continuation authority/path validation, redirect refusal, typed normalization, and complete-or-raise behavior.

The proposal has one responsibility: a complete single-region CDR read at the provider boundary. Cross-region orchestration, required-region health, inbound-leg eligibility, unknown-time consumer policy, mirror preservation, and ticket creation remain assigned to NC-492 (`VR-006:23-27,112-116,181-184`; `NC-492:95-175,374-381`). The direct Pydantic declaration supports the same public row contract and is not an orthogonal feature.

Every behavior is testable offline. The criteria specify exact URLs and query parameters, environment isolation, validation-before-client/HTTP, five-field normalization, cutoff behavior, continuation preservation, cap exhaustion, malformed and cross-authority links, redirect refusal, later-page failure, timeout bounds, request counts, legacy behavior, distribution imports, and requirement markers (`VR-006:139-174`). These yield behavior-caused RED tests rather than import or fixture failures, as required by the testability rubric (`judge-fr/doctrine.md:58-61`).

The research record satisfies the prospective evidence gate. It dispositions five genuine alternatives and precedents, preserves the corrected `limit=200` diagnosis, records an offline SDK observation without overstating server precision, and answers `is_this_a_graph` (`VR-006:16-21,118-137`; `judge-fr/doctrine.md:118-130`).

Strategic classification: **contrib/provider transport-boundary defect fix**. It has one named consuming workflow and an evidenced gap in the existing Twilio transport abstraction; it is neither a YAMLGraph framework primitive nor pattern-only documentation (`VR-006:8-12`; `judge-fr/doctrine.md:51-57`).

## Required revisions

None.

## Scope is frozen

| Deliverable | Surface |
|---|---|
| D-1 | `projects/voice_runtime/voice_runtime/transports/twilio_call.py`: `list_calls`, `TwilioCallRow`, and `TwilioCallListIncompleteError` exports, plus only a deprecation docstring on `list_recent_calls` |
| D-2 | `projects/voice_runtime/voice_runtime/transports/_twilio_client.py`: minimal explicit-host construction and strict validation needed by `list_calls`, while retaining the sole lazy Twilio client boundary |
| D-3 | One small transport model module only if required to remain within module-size limits |
| D-4 | Focused offline VR-006 behavioral and legacy-preservation tests |
| D-5 | `projects/voice_runtime/pyproject.toml`: add only `pydantic>=2.0,<3` as a direct runtime dependency and update release version metadata for 0.1.14 |
| D-6 | VR-006 implementation decisions/status, typed migration documentation, changelog/release metadata, and built-distribution import smoke |

Not authorized: CSAP code, pin, secret, deployment, rollout, health, mirror, eligibility, or ticket changes; `hangup_call` or any other VR-004 behavior/export; SMS, outbound-call, webhook-signature, media, STT, or worker-lifecycle changes; environment mutation; retries; partial-list returns; live or billable Twilio requests; dependency changes other than the direct Pydantic v2 declaration; direction filtering; cross-region orchestration; or weakening `list_recent_calls` behavior.

## Revised acceptance criteria

- [ ] AC-01: Behavioral RED then GREEN asserts exact US1 and IE1 GET URLs, account path independent of auth identity, explicit `To`, UTC instant-valued `StartTime>`, and `PageSize`; conflicting ambient credentials, region, and edge cannot alter requests.
- [ ] AC-02: Omitted required keywords raise `TypeError`. Invalid host/auth, blank destination, naive datetime, bool or out-of-range page bounds, and account SIDs containing non-ASCII/non-alphanumeric characters, whitespace/control characters, percent escapes, `/`, `\`, `?`, or `#` raise `ValueError` before client construction and HTTP. Diagnostics expose no auth values, and no unfiltered request is possible.
- [ ] AC-03: Offline row tests assert exactly `call_sid`, `status`, `start_time`, `direction`, and `caller`; both directions and anonymous caller labels are preserved; blank caller becomes `None`; aware timestamps become UTC epoch seconds; null timestamps remain `None`; known timestamps at or before the cutoff are excluded; missing/blank/wrong-type required strings and malformed or naive timestamps raise without skipping.
- [ ] AC-04: Two-page success follows continuation exactly once per page and preserves its PageToken/query without reapplying initial parameters. Complete empty success returns `[]`. Continuation after the cap raises `TwilioCallListIncompleteError` with exact `api_host` and `pages_fetched` attributes and no partial return; tests cover `max_pages=1` and an empty page carrying continuation.
- [ ] AC-05: Relative and same-host absolute continuation links work. Malformed links and links changing scheme, authority, port, userinfo, account, collection path, or fragment fail before an alternate request. Redirect responses fail without following `Location`. Page-two HTTP, transport, parse, or row-validation failure propagates and discards accumulated rows.
- [ ] AC-06: Every page uses the default 15-second or valid overridden `TWILIO_HTTP_TIMEOUT`; nonnumeric, nonfinite, zero, and negative overrides fail before HTTP. There are no retries and no more than `max_pages` requests.
- [ ] AC-07: `list_recent_calls` retains its 0.1.13 signature, implicit configuration, dictionary shape, zero-for-missing-time behavior, `limit=200`, and missing-credential `[]`; only its deprecation docstring changes. Existing Twilio transport assertions remain intact. No SMS, outbound, hangup, signature, media, or STT behavior changes.
- [ ] AC-08: New and changed behavioral tests carry `@pytest.mark.req("VR-006")`; behavioral RED precedes GREEN, with only a minimal importable API skeleton permitted first; the full offline voice-runtime suite passes.
- [ ] AC-09: `pyproject.toml` declares exactly `pydantic>=2.0,<3` as the only dependency change. A clean environment installed from the built, non-editable 0.1.14 distribution imports `list_calls`, `TwilioCallRow`, and `TwilioCallListIncompleteError` without requiring VR-004 exports.
- [ ] AC-10: VR-006 records implementation decisions/status, provides a typed migration example, and adds its changelog and release metadata. Joint publication may include VR-004 only after VR-004 has separate promoted authority and enforcement; VR-006 implementation and tests neither modify nor require hangup behavior.
- [ ] AC-11: Before NC-492 consumer enforcement, its plan consumes typed fields, excludes `start_time=None` rows from stub creation, and treats `TwilioCallListIncompleteError` as a failed region. This records the consumer seam but authorizes no CSAP edit under VR-006.

## Conditions for enforcement

| # | Condition | Severity |
|---|---|---|
| C-1 | Keep `_twilio_client.py` the sole lazy Twilio SDK construction boundary and preserve VR-002's bounded-request contract for all existing call sites. | GATE |
| C-2 | Produce behavior-caused RED commits before GREEN; import errors, missing fixtures, and live-network failures are not valid RED witnesses. | GATE |
| C-3 | Never return accumulated rows after cap exhaustion or any later-page failure, and never request an unvalidated continuation or follow a redirect. | GATE |
| C-4 | Preserve `list_recent_calls` behavior exactly apart from its deprecation docstring; migration is explicit, not silently substituted. | GATE |
| C-5 | Add only `pydantic>=2.0,<3`; no other dependency or unrelated callable change is authorized. | GATE |
| C-6 | Do not edit CSAP, deploy, access credentials, induce calls, make billable requests, or implement consumer orchestration/health/eligibility/mirror/ticket policy under this authority. | GATE |
| C-7 | Do not modify `hangup_call` or claim VR-004 acceptance under this judgement; joint release coordination does not merge the two implementation authorities. | GATE |
| C-8 | Human review must promote this advisory draft before implementation authority activates. | GATE |

Authority granted: after human promotion of this draft, implement and release VR-006's explicit, destination-filtered, instant-bounded, typed, complete-or-raise single-region CDR read contract, its minimal shared-client support, and its direct Pydantic v2 dependency within the frozen surfaces.

---

## Historical round 1 — superseded by the round-2 verdict above

# Judgement: VR-006 Explicit-region, destination-filtered, complete CDR reads

**Verdict:** APPROVED WITH REVISIONS — the provider-boundary fix is evidenced, cohesive, and directly testable, but authority activates only after the FR makes account-path validation URI-safe and decouples VR-006 enforcement authority from VR-004's separate approval.

**Reviewed against:** `projects/voice_runtime/feature-requests/VR-006-region-aware-cdr-read.md`; cited `projects/voice_runtime/feature-requests/VR-002-twilio-http-timeout.md` and `projects/voice_runtime/feature-requests/VR-002-twilio-http-timeout.judgement.md`; cited `projects/voice_runtime/feature-requests/VR-004-region-aware-hangup.md`; cited `projects/voice_runtime/voice_runtime/transports/twilio_call.py`, `projects/voice_runtime/voice_runtime/transports/_twilio_client.py`, and `projects/voice_runtime/tests/test_vr002_twilio_http_timeout.py`; cited sibling-repository baseline `customer-service-agent-platform@7f0442bc8ccf2bbd54f76f691937b34ee88adc94`, specifically `feature-requests/NC-492-reconciler-restoration.md`, `feature-requests/NC-493-region-aware-reaper-hangup.md`, `feature-requests/evidence/NC-488-reconciler-diagnosis.md`, `troubleshooting/gather_twilio.py`, and `feature-requests/VBOT-97-twilio-sms-standalone-component.md`; judge doctrine `.github/skills/judge-fr/doctrine.md`; judgement template `.github/skills/judge-fr/judgement.template.md`; repo doctrine `.github/copilot-instructions.md`.

## What is sound

The defect is real and bounded. The current callable reads ambient credentials and destination, uses `limit=200`, and emits only three untyped fields (`twilio_call.py:150-175`). The incident evidence independently shows an account-wide `To=None` request, rejected deployed credentials, and inbound legs present only in IE1 (`NC-488-reconciler-diagnosis.md:13-25`, `27-43`, `51-76`). A new explicit callable is smaller and safer than changing deployed `list_recent_calls`; preserving that callable while requiring deliberate migration keeps the fix at the provider boundary (`VR-006:99-109`).

The proposal is internally cohesive: explicit host/auth/account identity, destination and instant boundaries, typed normalization, continuation validation, and fail-without-partial-result behavior are all facets of one complete regional CDR read. Cross-region health, inbound-leg eligibility, mirror preservation, and ticket policy remain explicitly assigned to NC-492 rather than bundled here (`VR-006:23-27`, `93-109`; `NC-492:85-100`, `160-175`). This satisfies single responsibility rather than requiring a split.

The architecture follows established boundaries. VR-002 made `_twilio_client.py` the sole lazy, bounded Twilio client construction point (`_twilio_client.py:1-6`, `16-26`; `VR-002-twilio-http-timeout.judgement.md:49-65`), and VR-004 independently specifies the same explicit-host helper and separation of auth identity from account-path identity (`VR-004:38-52`). The cited collector proves the essential wire pattern: separate regional auth/account path, an initial filtered request, and continuation query preservation by omitting initial params on later pages (`gather_twilio.py:115-150`). The FR improves that precedent by validating continuation authority and refusing partial success.

The acceptance surface is substantially mechanical. Exact URLs and parameters, environment isolation, validation-before-HTTP, five-field normalization, strict cutoff behavior, two-page continuation, empty success, cap exhaustion, later-page failure, redirect refusal, bounded timeout, no retry, request count, legacy behavior, requirement markers, and built-distribution imports all admit direct offline assertions (`VR-006:132-158`). A minimal API skeleton before behavioral RED also avoids collection-error pseudo-RED, satisfying the doctrine's testability rule (`judge-fr/doctrine.md:58-61`).

The research record is substantive rather than shape-only: it dispositions five genuine alternatives, preserves the correction from an earlier paging diagnosis, cites concrete precedent, and answers `is_this_a_graph` (`VR-006:16-21`, `111-130`). It therefore satisfies the prospective research gate (`judge-fr/doctrine.md:118-130`).

Strategic classification: **contrib/provider transport-boundary defect fix**. It has one named consuming workflow and an evidenced gap in the existing transport abstraction; it is not a YAMLGraph framework primitive (`VR-006:8-9`, `125-130`; `judge-fr/doctrine.md:51-57`).

## Required revisions

### R-1: Freeze account identity as a safe URL path segment

Replace the ambiguous requirement that `account_sid` merely exclude "path/query delimiters" (`VR-006:41-45`) with a mechanically testable path-segment contract. Require a nonblank strict ASCII alphanumeric account SID and reject whitespace, control characters, percent escapes, `/`, `\`, `?`, and `#` before client construction. Add explicit RED cases proving each invalid class causes `ValueError` and zero HTTP requests. This closes the fragment/encoded-separator gap and makes the promised exact account Calls path (`VR-006:61-72`) true for the first request as well as continuations.

### R-2: Separate VR-006 authority from VR-004 release coordination

Revise AC-08 and the scope record so this judgement authorizes only VR-006's callable, row/exception exports, shared helper additions needed by that callable, tests, documentation, changelog, and release metadata. VR-004 remains a separate proposed change (`VR-004:1-7`) and owns all hangup semantics (`VR-006:160-167`). VR-006 tests and built-distribution smoke must import and exercise only VR-006-owned exports. A joint `0.1.14` publication may include VR-004 only after VR-004 receives its own implementation authority; VR-006 enforcement must neither edit `hangup_call` nor depend on an unauthorized VR-004 export.

## Scope is frozen

| Deliverable | Surface |
|---|---|
| D-1 | `projects/voice_runtime/voice_runtime/transports/twilio_call.py`: new `list_calls`, `TwilioCallRow`, `TwilioCallListIncompleteError` exports, plus only the deprecation docstring on `list_recent_calls` |
| D-2 | `projects/voice_runtime/voice_runtime/transports/_twilio_client.py`: minimal explicit-host client construction and strict timeout validation shared at the existing sole construction boundary |
| D-3 | One small transport model module only if needed to keep module-size limits |
| D-4 | Focused offline VR-006 tests plus minimal legacy-test updates needed for the deprecation assertion |
| D-5 | VR-006 migration documentation, decision/status record, changelog fragment, and voice-runtime release metadata |
| D-6 | Built-distribution smoke for the VR-006 callable, model, and exception exports |

Not authorized: any CSAP edit or pin/rollout change; `hangup_call` or other VR-004 behavior; SMS, outbound-call, webhook-signature, media, STT, or worker-lifecycle changes; credential or deployment operations; environment mutation; retries; partial-list returns; live or billable Twilio requests; new dependencies; regional orchestration or health policy; direction-based consumer eligibility; mirror or ticket behavior.

## Revised acceptance criteria

- [ ] AC-01: Behavioral RED then GREEN asserts the exact US1 and IE1 GET URLs, account path independent of auth identity, explicit `To`, UTC instant-valued `StartTime>`, and `PageSize`; conflicting ambient credentials, region, and edge cannot alter either request.
- [ ] AC-02: Omitted required keywords raise `TypeError`. Invalid host/auth, blank destination, naive datetime, bool or out-of-range page bounds, and account SIDs containing non-ASCII/non-alphanumeric characters, whitespace/control characters, percent escapes, `/`, `\`, `?`, or `#` raise `ValueError` before client construction and HTTP. Diagnostics and model reprs expose no auth values, and no unfiltered request is possible.
- [ ] AC-03: Offline row tests assert exactly `call_sid`, `status`, `start_time`, `direction`, and `caller`; both cited directions and anonymous caller labels are preserved; blank caller becomes `None`; aware timestamps become UTC epoch seconds; null timestamps remain `None`; known timestamps at or before the cutoff are excluded; missing/blank/wrong-type required fields and malformed or naive timestamps raise without skipping.
- [ ] AC-04: Two-page success follows continuation once per page and preserves its PageToken/query without reapplying initial params. Complete empty success returns `[]`. Continuation after the request cap raises `TwilioCallListIncompleteError` with exact `api_host` and `pages_fetched` attributes and no partial return; cover `max_pages=1` and an empty page carrying continuation.
- [ ] AC-05: Relative and same-host absolute continuation links work. Malformed links and links changing scheme, authority, port, userinfo, account, collection path, or fragment fail before an alternate request. Redirect responses fail without following `Location`. Page-two HTTP, transport, payload-parse, or row-validation failure propagates and discards accumulated rows.
- [ ] AC-06: Every page uses the default 15-second or valid overridden `TWILIO_HTTP_TIMEOUT`; nonnumeric, nonfinite, zero, and negative overrides fail before HTTP. There are no retries and no more than `max_pages` requests.
- [ ] AC-07: `list_recent_calls` retains its 0.1.13 signature, implicit configuration, dictionary shape, zero-for-missing-time behavior, `limit=200`, and missing-credential `[]`; only a deprecation docstring changes. Existing Twilio transport tests pass without weakened assertions. No SMS, outbound, hangup, signature, media, or STT behavior changes.
- [ ] AC-08: New and changed behavioral tests carry `@pytest.mark.req("VR-006")`; behavioral RED precedes GREEN, with only a minimal importable API skeleton permitted first; the full offline voice-runtime suite passes.
- [ ] AC-09: The VR-006 FR records implementation decisions and status, provides a typed migration example, adds its changelog/release metadata, and proves a built distribution imports `list_calls`, `TwilioCallRow`, and `TwilioCallListIncompleteError`. The smoke test does not require VR-004 exports, and no hangup change enters the release without separate VR-004 authority.
- [ ] AC-10: The NC-492 plan, before its own enforcement, explicitly consumes typed fields, excludes unknown-time rows from stub creation, and treats `TwilioCallListIncompleteError` as a failed region; this criterion records the declared consumer seam but authorizes no CSAP edit under VR-006.

## Conditions for enforcement

| # | Condition | Severity |
|---|---|---|
| C-1 | Fold R-1 and R-2 into VR-006 before implementation authority is exercised. | GATE |
| C-2 | Keep `_twilio_client.py` the sole lazy Twilio client construction boundary and preserve the bounded-timeout contract established by VR-002. | GATE |
| C-3 | Produce behavior-caused RED commits before GREEN; import errors, missing fixtures, and live-network failures are not valid RED witnesses. | GATE |
| C-4 | Never return accumulated rows after cap exhaustion or any later-page failure, and never follow an unvalidated continuation or redirect. | GATE |
| C-5 | Do not edit CSAP, deploy, access credentials, make billable calls, or implement consumer health/eligibility/mirror/ticket policy under this authority. | GATE |
| C-6 | Do not modify `hangup_call` or claim VR-004 acceptance under this judgement; a joint release may carry VR-004 only after its separate authority exists. | GATE |
| C-7 | Preserve `list_recent_calls` behavior exactly apart from its deprecation docstring; migration is explicit, not delegated or silently substituted. | GATE |

Authority granted: after R-1 and R-2 are folded into VR-006, implement and release the explicit, destination-filtered, instant-bounded, typed, complete-or-raise single-region CDR read contract and its minimal shared-client support, without consumer or hangup changes.
