# VR-004 — Explicit-region Twilio hangup with honest terminal detection

**Priority:** HIGH
**Type:** Bug fix + explicit provider contract
**Status:** Implemented on branch (2026-09-08) — verified offline; PR review and publication pending.
Final verdict recorded for human review in [judgement history](VR-004-region-aware-hangup.judgement.md).
**Requested:** 2026-08-17
**Target release:** 0.1.14, shared with VR-006; 0.1.13 already shipped VR-005
**First consumer / first event:** CSAP NC-493, when the supervisor reaper
ends a wedged call before recycling its worker.
**Research:** [Dispositioned alternatives](#research-and-prior-art).
**Prior art:** VR-002, VR-003, VR-006; CSAP NC-430, NC-492, NC-493 and
VBOT-97 Amendment 3, dispositioned below.

## Problem and ideal result

Current `hangup_call` uses account SID/token and the SDK's default host
unless ambient region/edge variables change it. It treats every 404 as
success. Absence from one region is not terminal evidence. CSAP's NC-488
diagnosis records rejected REST credentials and inbound legs in IE1,
including TEST/STG; the older us1 TEST/STG interpretation is historical.

**Ideal Result:** callers supply regional credentials and account identity;
bounded requests run in that order and return only on positive terminal
evidence. Failure raises, leaving worker-recycling policy to the consumer.

## Proposed solution

Extend the public callable in `voice_runtime.transports.twilio_call`:

```python
hangup_call(call_sid: str, *, regions: tuple[TwilioRegion, ...] | None = None) -> None
```

`TwilioRegion`, exported from the same module, is a frozen Pydantic model
with `api_host: str`, `account_sid: str`, `auth: tuple[str, str]`.
Exclude auth from repr and hide input values in validation messages.
Declare `pydantic>=2.0,<3` as a direct runtime dependency in `pyproject.toml`:
FastAPI currently supplies it transitively, but this public model makes it
our own dependency. This explicit dependency-contract change replaces the
first draft's incorrect "no new dependency" claim. VR-006 needs the same
declaration, added only once; no other dependency change is authorized.

### Explicit mode

- Validate the complete, nonempty tuple with distinct hosts before HTTP.
      Require nonblank strict ASCII alphanumeric call/account SIDs and
  exactly two nonblank auth strings. Invalid values raise `ValueError`
  (including Pydantic `ValidationError`); diagnostics do not expose auth.
      Before client construction reject whitespace, control/non-ASCII characters,
      percent escapes, `/`, `\`, `?` and `#` in either SID.
- Accept `api.twilio.com` or `api.<edge>.<region>.twilio.com` with lowercase
  DNS labels. Reject schemes, ports, userinfo, paths, query strings and
  unrelated hosts. No hardcoded list of supported regions.
- Extend `_twilio_client.py` with a shared explicit-host construction helper,
  also used by VR-006. Auth identity is separate from account path SID.
  Clear the SDK client's ambient region/edge overrides and bind its host;
  never mutate `os.environ`. Ambient credentials cannot replace inputs.
- Issue `POST https://<api_host>/2010-04-01/Accounts/<account_sid>/Calls/<call_sid>.json`
  with `Status=completed`, at most once per host, in tuple order.

| Response | Required outcome |
|---|---|
| Successful SDK update (2xx) | Return `None`; no later region |
| HTTP 400 + Twilio 21220 | Return `None`; no later region |
| HTTP 404 + Twilio 20404 | Try next supplied host |
| All hosts return 404/20404 | Raise `TwilioCallNotFoundError(RuntimeError)`; never claim terminal success |
| Any other HTTP error or transport timeout | Propagate immediately; no next-region attempt or retry |

Export `TwilioCallNotFoundError` from `twilio_call`; its
`attempted_hosts: tuple[str, ...]` and message contain hosts, not credentials.

### Existing one-argument mode

`regions=None` preserves account SID/token environment loading, SDK ambient
region handling, missing-credential errors, 2xx and 400/21220 success.
**Only the 404 success rule changes:** 404/20404 raises the named exception
after the single attempt; other errors propagate. This corrects VR-003,
without turning the old entry point into a regional discovery mechanism.
For that exception, `attempted_hosts` is a one-item tuple containing the
actual normalized request hostname after SDK ambient region/edge handling,
not a guessed US1 default. Assert it against the final fake-HTTP request URL.

The media handler already catches hangup exceptions before WS close; retain
and test that path. Its token precheck, signature validation and graceful
credential wiring stay unchanged. This FR does not claim API-key-only media
teardown support. NC-493's reaper calls the new explicit mode separately.

### Timeout and consumer seam

Each request retains `TWILIO_HTTP_TIMEOUT` (default 15 seconds), lazy SDK
imports and VR-002's single construction boundary. Explicit mode rejects
invalid/nonfinite/nonpositive timeout values before requests. No retries;
at most N requests for N supplied regions. This is a per-request timeout,
not a hard end-to-end sweep deadline.

NC-493 builds the ordered tuple from NC-492's matrix: US1 then IE1, using
`TWILIO_US1_API_SID` / `TWILIO_US1_API_SECRET` and
`TWILIO_IE1_API_SID` / `TWILIO_IE1_API_SECRET`, plus separate account SID.
That environment wiring and completeness policy belong to CSAP. The library
does not parse these names; no auth token is required for explicit REST mode.
Webhook signing retains its separate account-token use.

## Research and prior art

CSAP committed baseline: `7f0442bc8ccf2bbd54f76f691937b34ee88adc94`.
Cross-repo sources require the sibling checkout; do not copy private incident
identifiers, phone numbers or secret fingerprints into this repository.

| Alternative / precedent | Observed evidence | Disposition |
|---|---|---|
| Existing account-token hangup, VR-003 | Source accepts every 404; NC-488 records REST 401 and mismatched regional legs | Retain entry point, correct terminal inference; not a repair alone |
| SMS single-active-region policy, VBOT-97 Amendment 3 | Separate API-key auth/account SID; both-region secret requirement caused a provisioning outage | Reuse identity separation, not policy: NC-493 explicitly needs ordered multi-region lookup |
| NC-430 diagnostic matrix | Collector queries US1 and Dublin IE1, separate account path SID | Consumer supplies its matrix; no CSAP imports or silent failed-region omission |
| SDK kwargs / base URL alone | SDK 9.10.9 offline probe: Dublin base URL plus ambient Sydney/AU1 emitted Sydney; clearing client overrides emitted Dublin | Explicit client isolation; test final URLs, not constructor kwargs |
| Separate REST implementation in CSAP | NC-493 Constraint 2 prohibits consumer Twilio clients; VR-002 owns construction | Reject duplicate provider code |

**Is this a graph?** No: deterministic HTTP transport, no LLM stage.
The 2026-09-08 probe used fake HTTP and dummy credentials, zero network;
55 focused voice tests and 58 CSAP SMS/collector tests passed.

## Acceptance criteria

- [ ] AC-01: Behavioral RED then GREEN: US1 404/20404 then IE1 2xx;
      assert exact URLs, POST, `Status=completed`, order, per-host auth and
      account SID paths. First-region 2xx stops after one request.
- [ ] AC-02: All-region 404/20404 raises `TwilioCallNotFoundError` with exact
      attempted hosts, no success return or terminal-success log.
- [ ] AC-03: 400/21220 in either region succeeds; 401, 403, 429, 500,
      other 400/404 codes and request timeout raise without later attempts.
- [ ] AC-04: Explicit API-key-only mode works without an auth token;
      conflicting ambient credentials/region/edge cannot alter either request.
      Invalid identifiers/hosts, empty or duplicate regions and blank auth
      fail before HTTP; model repr and diagnostics contain no auth values.
      Table-driven cases reject each forbidden SID class (including `/`,
      `\`, `?`, `#`, percent escapes, whitespace/control and non-ASCII)
      in both call/account SID before client construction.
- [ ] AC-05: Every explicit attempt has bounded default/overridden timeout;
      invalid/nonfinite/nonpositive values fail before HTTP. Lazy imports and
      `_twilio_client.py` as the sole construction boundary remain enforced.
- [ ] AC-06: Old mode retains missing-credential errors and positive success;
      404/20404 now raises. Replace VR-003's 404-success expectation and prove
      the media handler reaches its existing WS-close exception path without
      waiting its five-second REST-close window.
      With nondefault ambient region/edge, the exception's one-item
      `attempted_hosts` equals the actual normalized request hostname.
- [ ] AC-07: No SMS, outbound, STT, signature or `list_recent_calls` changes;
      full offline suite passes. Tests carry `@pytest.mark.req("VR-004")`;
      RED is behavioral, not a collection/import failure (minimal API skeleton
      may precede RED).
- [ ] AC-08: Record VR-004 decisions, changelog and migration example;
      built-distribution smoke imports `hangup_call`, `TwilioRegion` and
      `TwilioCallNotFoundError`, without requiring VR-006 exports. Declare
      `pydantic>=2.0,<3` directly and prove these exports work in a clean
      environment installed from the built distribution, not an editable tree.
      CSAP owns exact pins and TEST/STG evidence.
- [ ] AC-09: Coordinate publication as 0.1.14; a joint artifact may include
      VR-006 only after its separate judgement and enforcement. VR-004 tests
      do not depend on or assert VR-006 APIs. Either FR may implement its
      needed shared helper first; neither authority covers the other's callable.

## Scope and decisions (2026-09-08)

- Surfaces: `twilio_call.py`, `_twilio_client.py`, small model module if
      needed for size limits, tests, docs and release metadata, plus the explicit
      Pydantic v2 dependency declaration. No other dependency changes.
  Share explicit construction with VR-006 without changing old callers.
- NC-493 D-2 permits "already gone" after all-region absence; its AC-02
  forbids that claim. Follow **AC-02: failure**. NC-493 must fold that
  correction and this explicit tuple signature before consumer enforcement.
- Library work and NC-493 rollout do not wait on NC-492's mirror guard.
  Shared release does not authorize early reconciler deployment.
- No billable calls, induced failures, deployments, credential operations,
  signature hardening, worker lifecycle edits or consumer implementation.
- Round-1 revisions: exact old-mode hostname metadata, URI-safe SID validation
      and independent release acceptance are folded. Dependency finding confirmed:
      local Pydantic 2.13.4 / FastAPI 0.141.1, but no direct Pydantic declaration
      in 0.1.13. Owner authorized the revised plan and docs PR on 2026-09-08;
      round 2 independently approved the explicit dependency scope. This PR
      changes documents only, not the manifest or runtime; implementation remains
      a later task after human review of the final judgement.

## Implementation record (2026-09-08)

Owner authorized enforcement after merging docs PR #4. Behavioral RED
`599aedd` (76 failed / 13 passed) preceded GREEN `180c877`; the minimal
importable model/signature skeleton and approved Pydantic declaration were
included in RED, as permitted by the judgement. VR-006 owns the later read API.

AC-01..AC-07 have offline witnesses in `tests/test_vr004_region_hangup.py`
and the amended VR-003 media tests. The final combined suite is 589 passed,
1 skipped, 9 warnings; warnings are reported, not suppressed. AC-08's migration
example is executed by its own test. Clean non-editable wheel imports and fake
regional requests passed with SDK 9.11.0. AC-09's separate authority is preserved:
VR-004's example and wheel smoke import only its three exports. 0.1.14 is
prepared, not published. See [verification record](evidence/VR-004-VR-006-verification.md).

No production deviation from the approved hangup contract. `_twilio_client.py`
remains the only SDK construction boundary; typed models live in the small
`_twilio_models.py` module. No consumer, signature, deployment or secret edits.
PR review, human merge, tag/publication and consumer pin/rollout remain pending.

## Source references

- [VR-002](VR-002-twilio-http-timeout.md) and its judgement; [VR-003](VR-003-rest-first-call-end-31921.md) and its judgement; [VR-006](VR-006-region-aware-cdr-read.md).
- [NC-493](../../../customer-service-agent-platform/feature-requests/NC-493-region-aware-reaper-hangup.md), [NC-492](../../../customer-service-agent-platform/feature-requests/NC-492-reconciler-restoration.md).
- [NC-488 evidence](../../../customer-service-agent-platform/feature-requests/evidence/NC-488-reconciler-diagnosis.md), [NC-429 historical evidence](../../../customer-service-agent-platform/feature-requests/evidence/NC-429-zero-31921.md).
- [SMS precedent](../../../customer-service-agent-platform/feature-requests/VBOT-97-twilio-sms-standalone-component.md), [collector](../../../customer-service-agent-platform/troubleshooting/gather_twilio.py).
- [Call implementation](../voice_runtime/transports/twilio_call.py), [client](../voice_runtime/transports/_twilio_client.py), [media handler](../voice_runtime/transports/twilio_ws.py), [existing tests](../tests/test_vr003_rest_first_call_end_31921.py).
- [Dependency manifest](../pyproject.toml) (0.1.13, direct runtime dependencies).
