# Judgement: VR-004 Explicit-region Twilio hangup with honest terminal detection

**Verdict:** APPROVED — the revised FR is a clear, bounded provider-boundary repair whose dependency, legacy-mode, regional-routing, and independent-release contracts are now explicit and mechanically testable; this draft remains advisory until human-reviewed and promoted.

**Reviewed against:** `projects/voice_runtime/feature-requests/VR-004-region-aware-hangup.md`; existing promoted judgement `projects/voice_runtime/feature-requests/VR-004-region-aware-hangup.judgement.md`; committed predecessor of VR-004 at `projects/voice_runtime@3e5333b^`; `projects/voice_runtime/feature-requests/VR-002-twilio-http-timeout.md`; `projects/voice_runtime/feature-requests/VR-002-twilio-http-timeout.judgement.md`; `projects/voice_runtime/feature-requests/VR-003-rest-first-call-end-31921.md`; `projects/voice_runtime/feature-requests/VR-003-rest-first-call-end-31921.judgement.md`; `projects/voice_runtime/feature-requests/VR-006-region-aware-cdr-read.md`; `projects/voice_runtime/voice_runtime/transports/twilio_call.py`; `projects/voice_runtime/voice_runtime/transports/_twilio_client.py`; `projects/voice_runtime/voice_runtime/transports/twilio_ws.py`; `projects/voice_runtime/tests/test_vr002_twilio_http_timeout.py`; `projects/voice_runtime/tests/test_vr003_rest_first_call_end_31921.py`; `projects/voice_runtime/tests/test_twilio_call.py`; `projects/voice_runtime/pyproject.toml`; sibling-repository baseline `customer-service-agent-platform@7f0442bc8ccf2bbd54f76f691937b34ee88adc94`: `feature-requests/NC-493-region-aware-reaper-hangup.md`, `feature-requests/NC-492-reconciler-restoration.md`, `feature-requests/evidence/NC-488-reconciler-diagnosis.md`, `feature-requests/evidence/NC-429-zero-31921.md`, `feature-requests/VBOT-97-twilio-sms-standalone-component.md`, and `troubleshooting/gather_twilio.py`; judge doctrine `.github/skills/judge-fr/doctrine.md`; judgement template `.github/skills/judge-fr/judgement.template.md`; repo doctrine `.github/copilot-instructions.md`.

## What is sound

**Scope and single responsibility:** the FR repairs one causal chain: the current hangup path treats any 404 as terminal success (`twilio_call.py:114-147`), although the incident evidence records a live IE1 call receiving US1 404/20404 and remaining open until WebSocket fallback (`NC-429-zero-31921.md:16-34`). The later matrix confirms that inbound legs are in IE1 and that US1 and IE1 require distinct API-key credentials (`NC-488-reconciler-diagnosis.md:27-34,51-68`). Ordered explicit regions, positive terminal evidence, and an exhaustion exception are the smallest coherent repair (`VR-004:14-24,43-70`). Consumer environment wiring, retries, deployment, worker lifecycle, and discovery remain excluded (`VR-004:88-101,161-173`). The direct Pydantic declaration is not an orthogonal feature: the FR makes a Pydantic model part of the public API (`VR-004:28-41`), while the manifest currently declares FastAPI, Uvicorn, Twilio, and HTTPX but not Pydantic (`pyproject.toml:5-21`).

**Consistency:** the response table has one unambiguous decision per provider outcome: 2xx and 400/21220 stop successfully, only 404/20404 advances, exhaustion raises, and every unrelated HTTP or transport error propagates immediately (`VR-004:61-70`). Existing one-argument mode preserves ambient configuration and positive-success behavior while changing only 404/20404 semantics (`VR-004:72-86`). The FR explicitly resolves NC-493's internal conflict in favor of failure on all-region absence (`VR-004:167-169`; `NC-493:73-80,111-128`). It also separates VR-004 acceptance from VR-006 authority while allowing publication coordination only after VR-006 is independently judged and enforced (`VR-004:150-159`).

**Measurability and testability:** AC-01 through AC-09 specify observable URLs, methods, bodies, request order, auth/path identity, stopping rules, exact exception metadata, validation-before-client/HTTP, timeout behavior, final ambient-selected hostname, built-wheel imports, and forbidden adjacent changes (`VR-004:121-159`). The RED must be behavioral rather than an import or collection failure (`VR-004:146-149`), satisfying the doctrine's testability rule (`judge-fr/doctrine.md:58-61`) and the repository's RED-before-GREEN law (`.github/copilot-instructions.md:197`). Existing tests already expose the request-time timeout, terminal-exception, media fallback, and off-loop seams (`test_vr002_twilio_http_timeout.py:124-151`; `test_vr003_rest_first_call_end_31921.py:157-212,278-303,306-352`).

**Feasibility and architecture alignment:** `_twilio_client.py` is already the sole lazy Twilio SDK construction boundary with the configurable per-request timeout (`_twilio_client.py:1-26`), and `hangup_call` is already a thin call-resource update wrapper (`twilio_call.py:114-147`). The media handler already catches any hangup exception and proceeds directly to WebSocket close rather than entering the five-second successful-hangup wait (`twilio_ws.py:135-178`). Extending the client boundary with explicit host/auth construction therefore conforms before extending and normalizes provider routing at the entry boundary, as repo doctrine requires (`.github/copilot-instructions.md:47-48,91-103,191-197`). Adding `pydantic>=2.0,<3` directly is feasible and honest: the package already requires FastAPI (`pyproject.toml:16-20`), but the revised FR no longer mistakes that transitive installation for its own declared public-model contract (`VR-004:34-41,174-180`).

**Research quality:** the committed FR dispositions five genuine alternatives or precedents: retain the old account-token entry point, reject SMS's single-active-region policy for this consumer, reuse the collector's auth/path separation, isolate SDK ambient routing, and reject a duplicate consumer-side Twilio client (`VR-004:103-115`). The cited collector independently demonstrates separate regional auth and account-path identity (`gather_twilio.py:47-83,113-150,213-226`), while VBOT-97 records why requiring inactive-region SMS secrets caused a deployment outage and why that policy does not transfer to a simultaneous-region lookup (`VBOT-97:282-324,338-346`). The FR also answers the required graph-fit question directly (`VR-004:117-119`).

Strategic classification: **contrib/provider transport-boundary defect fix**, not a framework primitive. It has two concrete uses - existing media teardown and NC-493's reaper - but is Twilio-specific and extends an established voice-runtime boundary rather than introducing a general abstraction (`VR-004:8-9,83-100`; `NC-493:21-25,61-91`).

## Required revisions

None. Round-1 R-1 through R-4 are mechanically folded: old-mode host metadata (`VR-004:79-81,140-145`), exact SID rejection classes (`VR-004:45-50,130-136`), VR-006-independent acceptance (`VR-004:150-159`), and explicit direct Pydantic dependency scope supported by the manifest evidence (`VR-004:34-41,150-165,174-189`; `pyproject.toml:16-21`).

## Scope is frozen

| Deliverable | Surface |
|---|---|
| D-1 | `projects/voice_runtime/voice_runtime/transports/twilio_call.py` |
| D-2 | `projects/voice_runtime/voice_runtime/transports/_twilio_client.py` |
| D-3 | One small model module under `projects/voice_runtime/voice_runtime/transports/` only if needed to keep module size within doctrine |
| D-4 | `projects/voice_runtime/pyproject.toml`, limited to `pydantic>=2.0,<3` and VR-004 release/version metadata |
| D-5 | Focused VR-004 tests plus the minimum update to the VR-003 404 expectation and media-fallback witness |
| D-6 | `projects/voice_runtime/feature-requests/VR-004-region-aware-hangup.md` implementation record, decisions, migration example, and status |
| D-7 | Voice-runtime changelog and built-distribution evidence for the authorized VR-004 exports |

Not authorized: VR-006 call-read implementation, exports, or tests; `list_recent_calls`; dependency changes other than `pydantic>=2.0,<3`; SMS or outbound-call behavior; STT; webhook signature handling; media credential rewiring; CSAP implementation, pins, secrets, worker lifecycle, or deployment; region discovery; retries/backoff; end-to-end sweep deadlines; billable/live Twilio calls; induced failures; environment mutation; or changes to CI, hooks, judge/review doctrine, or other enforcement infrastructure.

## Revised acceptance criteria

- [ ] AC-01: Behavioral RED then GREEN proves US1 404/20404 followed by IE1 2xx, asserting exact HTTPS URLs, POST, `Status=completed`, tuple order, per-host auth, and account SID path; first-host 2xx issues exactly one request.
- [ ] AC-02: All supplied hosts returning 404/20404 raises `TwilioCallNotFoundError`; its `attempted_hosts` and message contain the exact ordered normalized hostnames and no credential value, and no terminal-success log is emitted.
- [ ] AC-03: 400/21220 at any attempted host returns `None` and stops; 401, 403, 429, 500, other 400/404 codes, and request timeout propagate immediately without retry or later-host request.
- [ ] AC-04: Explicit mode validates the complete nonempty tuple before client construction or HTTP: hosts are distinct and match only `api.twilio.com` or `api.<edge>.<region>.twilio.com` with lowercase DNS labels; call/account SIDs are nonblank strict ASCII alphanumeric strings and table-driven cases reject percent escapes, `/`, `\`, `?`, `#`, whitespace, controls, and non-ASCII in both identifiers; auth contains exactly two nonblank strings. Invalid input raises `ValueError`, and model repr plus validation diagnostics expose no auth value.
- [ ] AC-05: Conflicting ambient credentials, region, and edge cannot alter explicit-mode auth or final URLs. Explicit API-key auth works without an account auth token, client routing overrides are cleared without mutating `os.environ`, and the account path SID remains separate from authentication identity.
- [ ] AC-06: Every explicit attempt uses the default or overridden `TWILIO_HTTP_TIMEOUT`; invalid, nonfinite, or nonpositive values fail before HTTP. Lazy Twilio imports remain, `_twilio_client.py` remains the sole SDK construction boundary, no retry is added, and at most N requests occur for N regions.
- [ ] AC-07: Existing one-argument mode preserves environment credential loading, missing-credential errors, ambient SDK routing, 2xx success, and 400/21220 success. Its single 404/20404 raises `TwilioCallNotFoundError` with the actual normalized request hostname as the sole `attempted_hosts` item, asserted against the final fake-HTTP request URL; the media handler immediately reaches its existing WebSocket-close exception fallback without waiting `REST_CLOSE_WAIT_S`.
- [ ] AC-08: No SMS, outbound, STT, signature, or `list_recent_calls` behavior changes. Existing focused and full offline tests pass without weakened assertions; every new or changed behavioral test carries `@pytest.mark.req("VR-004")`; RED is behavioral, with only the minimal importable API skeleton permitted before RED.
- [ ] AC-09: The FR records decisions and implementation status; voice-runtime records the change and migration example; `pyproject.toml` declares exactly `pydantic>=2.0,<3` as the only dependency change; a clean environment installed from the built distribution imports `hangup_call`, `TwilioRegion`, and `TwilioCallNotFoundError` without requiring VR-006 exports.
- [ ] AC-10: Publication as 0.1.14 may include VR-006 only after VR-006 is separately judged and enforced. VR-004 implementation, tests, and built-distribution smoke do not implement, import, or assert VR-006-owned APIs as a condition of this authority.

## Conditions for enforcement

| # | Condition | Severity |
|---|---|---|
| C-1 | Commit behavioral RED before production implementation; collection or import failure is not the witness. | GATE |
| C-2 | Preserve the sole lazy, bounded SDK construction boundary in `_twilio_client.py`; explicit routing may extend it but no second Twilio client path may be introduced. | GATE |
| C-3 | Treat only 2xx and 400/21220 as positive terminal evidence; regional 404/20404 is absence, and exhaustion remains failure. | GATE |
| C-4 | Do not consume ambient credentials or routing in explicit mode, mutate environment state, retry, or continue after any error other than 404/20404. | GATE |
| C-5 | The only dependency change authorized is the direct runtime declaration `pydantic>=2.0,<3`; no other manifest or lock dependency may change under VR-004. | GATE |
| C-6 | Do not modify CSAP or VR-006 implementation surfaces under this authority; consumer wiring, pins, secrets, rollout, and acceptance evidence remain separately governed. | GATE |
| C-7 | No billable call, induced failure, deployment, credential read/change, or live Twilio request is authorized; use deterministic fake-HTTP witnesses. | GATE |
| C-8 | Human review must promote this advisory draft before enforcement begins; promotion is the second verdict in the canonical judgement file and closes later judge rounds for this FR under the round sentinel (`judge-fr/doctrine.md:133-142`). | GATE |

Authority granted: after human review promotes this draft, enforcement may implement the explicit ordered-region Twilio hangup contract, honest not-found exception, narrowly required old-mode 404 correction, direct Pydantic v2 declaration, tests, and release evidence within the frozen voice-runtime surfaces.

---

## Historical round 1 — superseded by the round-2 verdict above

# Judgement: VR-004 Explicit-region Twilio hangup with honest terminal detection

**Verdict:** APPROVED WITH REVISIONS — the regional hangup contract is a sound provider-boundary fix, but authority activates only after R-1 through R-4 are folded into the FR.

**Reviewed against:** `projects/voice_runtime/feature-requests/VR-004-region-aware-hangup.md`; `projects/voice_runtime/feature-requests/VR-002-twilio-http-timeout.md`; `projects/voice_runtime/feature-requests/VR-002-twilio-http-timeout.judgement.md`; `projects/voice_runtime/feature-requests/VR-003-rest-first-call-end-31921.md`; `projects/voice_runtime/feature-requests/VR-003-rest-first-call-end-31921.judgement.md`; `projects/voice_runtime/feature-requests/VR-006-region-aware-cdr-read.md`; `projects/voice_runtime/voice_runtime/transports/twilio_call.py`; `projects/voice_runtime/voice_runtime/transports/_twilio_client.py`; `projects/voice_runtime/voice_runtime/transports/twilio_ws.py`; `projects/voice_runtime/tests/test_vr003_rest_first_call_end_31921.py`; sibling-repository baseline `customer-service-agent-platform@7f0442bc8ccf2bbd54f76f691937b34ee88adc94`: `feature-requests/NC-493-region-aware-reaper-hangup.md`, `feature-requests/NC-492-reconciler-restoration.md`, `feature-requests/evidence/NC-488-reconciler-diagnosis.md`, `feature-requests/evidence/NC-429-zero-31921.md`, `feature-requests/VBOT-97-twilio-sms-standalone-component.md`, and `troubleshooting/gather_twilio.py`; judge doctrine `.github/skills/judge-fr/doctrine.md`; judgement template `.github/skills/judge-fr/judgement.template.md`; repo doctrine `.github/copilot-instructions.md`.

## What is sound

**Scope and single responsibility:** the FR addresses one causal chain: the current one-host hangup treats regional absence as terminal success, so a live IE1 call can survive a US1 404. The current implementation catches every 404 as success (`twilio_call.py:133-144`), while the incident record shows a live call receiving 404/20404, waiting five seconds, and falling back to server-side WS close (`NC-429-zero-31921.md:20-24`). The later matrix confirms that inbound legs exist in IE1 and that the two regions require distinct API-key credentials (`NC-488-reconciler-diagnosis.md:27-34,62-64`). Explicit ordered regions plus an all-regions-not-found failure is the minimum repair; retries, discovery, consumer policy, and deployment are correctly excluded (`VR-004:38-91,139-150`).

**Consistency:** the response table is internally coherent: 2xx and 400/21220 are positive terminal evidence, 404/20404 advances only to the next supplied host, exhaustion raises, and unrelated provider or transport failures stop immediately (`VR-004:54-63`). The FR explicitly resolves NC-493's contradictory prose in favor of its stricter AC-02 (`VR-004:144-146`): NC-493 D-2 permits an all-region "already gone" claim (`NC-493:73-80`), while NC-493 AC-02 calls the same outcome failure (`NC-493:113-118`). The revisions below remove the remaining local ambiguities without changing the direction.

**Measurability and testability:** AC-01 through AC-07 define deterministic, offline assertions for final URLs, request method/body, ordering, per-host identity, stop conditions, error classification, validation-before-HTTP, timeout bounds, legacy behavior, and the media fallback (`VR-004:111-134`). Existing tests already expose the exact seams: 404 and 400/21220 predicates (`test_vr003_rest_first_call_end_31921.py:285-300`), immediate REST-failure fallback (`test_vr003_rest_first_call_end_31921.py:157-181`), bounded close fallback (`test_vr003_rest_first_call_end_31921.py:184-212`), and off-loop execution (`test_vr003_rest_first_call_end_31921.py:311-352`). Behavioral RED tests can therefore be written without network access or import-failure theatre.

**Feasibility and architecture alignment:** VR-002 already established `_twilio_client.py` as the sole lazy, bounded Twilio SDK construction boundary (`VR-002-twilio-http-timeout.judgement.md:13-18,57-67`; `_twilio_client.py:16-26`). The current call path is already a thin SDK wrapper (`twilio_call.py:114-146`), and the media handler already converts any hangup exception into immediate WS fallback before entering its five-second success wait (`twilio_ws.py:140-167`). The proposed explicit-client helper extends that existing boundary rather than creating a consumer-side Twilio client. This follows the repo laws to normalize at the external boundary, remediate all occurrences, honor existing patterns, and require behavioral RED before GREEN (`.github/copilot-instructions.md:41-43,67,191,197`).

The prior-art research is substantive rather than ceremonial. It dispositions the old account-token path, the SMS single-active-region policy, the existing two-region collector, SDK host-override behavior, and a duplicate CSAP implementation (`VR-004:93-107`). The collector independently demonstrates separate account-path identity, per-region auth, and the US1/IE1 host matrix (`gather_twilio.py:47-76,213-219`); VBOT-97 shows why its single-active-region secret policy must not be copied into this ordered multi-region consumer (`VBOT-97:282-323,338-344`).

Strategic classification: **contrib/provider transport-boundary defect fix**, not a YAMLGraph framework primitive. It has two concrete call sites—the existing media teardown and NC-493's reaper—but remains Twilio-specific and fits the established voice-runtime transport abstraction (`VR-004:7-9,73-90`; `NC-493:21-24,82-90`).

## Required revisions

### R-1: Define old-mode attempted-host metadata

Amend the existing one-argument mode and AC-06 to state that when its single 404/20404 is converted to `TwilioCallNotFoundError`, `attempted_hosts` is a one-item tuple containing the actual normalized request hostname selected by the SDK after ambient region/edge handling. Add a no-network witness with conflicting ambient region/edge values that asserts both the final request URL and this exact exception attribute. The public exception currently promises host metadata (`VR-004:62-63`), but only explicit-mode exhaustion has an exact-host assertion (`VR-004:116-117`), while old mode deliberately preserves ambient host selection (`VR-004:65-71`).

### R-2: Enumerate identifier delimiters

Replace "without path/query delimiters" with the exact forbidden characters `/`, `\`, `?`, and `#` for both call SID and account SID. Add table-driven validation-before-HTTP assertions for each character in each identifier. The current phrase (`VR-004:40-43`) is not a mechanical predicate and permits enforcer and reviewer to choose different definitions of an invalid identifier.

### R-3: Remove VR-006 deliverables from VR-004 acceptance

Revise AC-08 so VR-004 requires only its own decisions, changelog, migration example, release metadata, and built-distribution imports for `hangup_call`, `TwilioRegion`, and `TwilioCallNotFoundError`. Keep a joint 0.1.14 publication as a coordination condition that activates only after VR-006 receives and satisfies its own authority; do not require VR-006's API/model imports to pass VR-004. VR-004 currently requires both APIs in its smoke test (`VR-004:135-137`) even though VR-006 remains proposed without implementation authority (`VR-006:5-10`) and owns a separate call-read contract (`VR-006:34-36,156-164`).

### R-4: Evidence the no-new-dependency claim

Add a source citation to the committed voice-runtime dependency manifest proving Pydantic is already a direct runtime dependency. The FR introduces a public Pydantic model (`VR-004:34-36`) while asserting "No new dependency" (`VR-004:141-143`), but none of the cited implementation or precedent files establishes that package contract. If the manifest does not already declare Pydantic directly, this judgement grants no authority to add it; re-plan and re-enter judgement with the dependency change made explicit.

## Scope is frozen

| Deliverable | Surface |
|---|---|
| D-1 | `projects/voice_runtime/voice_runtime/transports/twilio_call.py` |
| D-2 | `projects/voice_runtime/voice_runtime/transports/_twilio_client.py` |
| D-3 | One small model module under `projects/voice_runtime/voice_runtime/transports/` only if required to keep module size within doctrine |
| D-4 | Focused VR-004 tests plus the minimum update to the VR-003 404 expectation and media-fallback witness |
| D-5 | `projects/voice_runtime/feature-requests/VR-004-region-aware-hangup.md` revision, implementation record, migration example, and status |
| D-6 | Voice-runtime changelog, version, packaging, and built-distribution metadata for the authorized VR-004 exports |

Not authorized: VR-006 call-read implementation or tests; `list_recent_calls`; SMS or outbound-call behavior; STT; webhook signature handling; media credential rewiring; CSAP implementation, pins, secrets, worker lifecycle, or deployment; region discovery; retries/backoff; end-to-end sweep deadlines; billable/live Twilio calls; induced failures; new dependencies; environment mutation; or changes to judge/review/enforcement infrastructure.

## Revised acceptance criteria

- [ ] AC-01: Behavioral RED then GREEN proves US1 404/20404 followed by IE1 2xx, asserting exact HTTPS URLs, POST, `Status=completed`, tuple order, per-host auth, and account SID path; first-host 2xx issues exactly one request.
- [ ] AC-02: All supplied hosts returning 404/20404 raises `TwilioCallNotFoundError`; its `attempted_hosts` and message contain the exact ordered normalized hostnames and no credential value, and no terminal-success log is emitted.
- [ ] AC-03: 400/21220 at any attempted host returns `None` and stops; 401, 403, 429, 500, other 400/404 codes, and request timeout propagate immediately without retry or later-host request.
- [ ] AC-04: Explicit mode validates the complete nonempty tuple before HTTP: hosts are distinct and match only the authorized Twilio host forms; call/account SIDs are nonblank and reject `/`, `\`, `?`, and `#`; auth contains exactly two nonblank strings. Invalid input raises `ValueError`, and model repr plus validation diagnostics expose no auth value.
- [ ] AC-05: Conflicting ambient credentials, region, and edge cannot alter explicit-mode auth or final URLs. Explicit API-key auth works without an account auth token, the SDK client's routing overrides are cleared without mutating `os.environ`, and the account path SID remains separate from authentication identity.
- [ ] AC-06: Every explicit attempt uses the default or overridden `TWILIO_HTTP_TIMEOUT`; invalid, nonfinite, or nonpositive values fail before HTTP. Lazy Twilio imports remain, `_twilio_client.py` remains the sole SDK construction boundary, no retry is added, and at most N requests occur for N regions.
- [ ] AC-07: Existing one-argument mode preserves environment credential loading, missing-credential errors, ambient SDK routing, 2xx success, and 400/21220 success. Its single 404/20404 raises `TwilioCallNotFoundError` with the actual normalized request host as the sole `attempted_hosts` item; the media handler immediately reaches its existing WS-close exception fallback without waiting `REST_CLOSE_WAIT_S`.
- [ ] AC-08: No SMS, outbound, STT, signature, or `list_recent_calls` behavior changes. Existing focused tests pass without weakened assertions; every new or changed behavioral test carries `@pytest.mark.req("VR-004")`; RED is behavioral, with only the minimal importable API skeleton permitted before RED.
- [ ] AC-09: The FR records decisions and implementation status; voice-runtime records the change and migration example; a built distribution imports `hangup_call`, `TwilioRegion`, and `TwilioCallNotFoundError`. Pydantic is evidenced as an existing direct runtime dependency, and no dependency manifest is changed.
- [ ] AC-10: Publication as joint 0.1.14 occurs only after VR-006 is separately judged and enforced. VR-004 implementation and tests do not implement, import, or assert VR-006-owned APIs as a condition of this authority.

## Conditions for enforcement

| # | Condition | Severity |
|---|---|---|
| C-1 | Fold R-1 through R-4 into VR-004 before implementation authority is exercised. | GATE |
| C-2 | Commit behavioral RED before production implementation; collection/import failure is not the witness. | GATE |
| C-3 | Preserve the sole lazy, bounded SDK construction boundary in `_twilio_client.py`; explicit routing may extend it but no second Twilio client path may be introduced. | GATE |
| C-4 | Treat only 2xx and 400/21220 as positive terminal evidence; regional 404/20404 is absence, and exhaustion remains failure. | GATE |
| C-5 | Do not consume ambient credentials or routing in explicit mode, mutate environment state, retry, or continue after any error other than 404/20404. | GATE |
| C-6 | Do not modify CSAP or VR-006 implementation surfaces under this authority; their consumer wiring, pins, rollout, and acceptance evidence remain separately governed. | GATE |
| C-7 | Do not add Pydantic or any other dependency under this judgement; if Pydantic is not already a direct runtime dependency, stop and re-plan. | GATE |
| C-8 | Human review is required before this draft is promoted; this artifact is advisory and grants no authority by itself. | GATE |

Authority granted: after the required revisions are folded and human review promotes the judgement, implement the explicit ordered-region Twilio hangup contract, honest not-found exception, and the narrowly required old-mode 404 correction within the frozen voice-runtime surfaces.
