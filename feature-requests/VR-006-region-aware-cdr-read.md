# VR-006 — Explicit-region, destination-filtered, complete CDR reads

**Priority:** HIGH
**Type:** Bug fix + new provider contract
**Status:** Proposed — round-1 revisions folded; dependency re-plan awaits re-judgement
**Requested:** 2026-09-08
**Target release:** 0.1.14, shared with VR-004
**First consumer / first event:** CSAP NC-492 PR-2, when the guarded
reconciler fetches both regions' inbound call records for its tier.
**Research:** [Dispositioned alternatives](#research-and-prior-art).
**Prior art:** VR-002, VR-004; CSAP NC-395/NC-428, NC-430, NC-492,
NC-488 evidence and VBOT-97 Amendment 3, dispositioned below.

## Problem and ideal result

`list_recent_calls` in 0.1.13 reads implicit account credentials and
`TWILIO_PHONE_NUMBER`, returning SID/status/epoch start time only. Without
a number it can query account-wide; without ambient region settings it
queries US1, whereas the consumer's inbound legs are in IE1. It uses
**`limit=200`**, not unlimited pagination: silent truncation at 200 records
is the defect. The initial draft's unbounded-paging diagnosis was incorrect.

**Ideal Result:** one explicit regional fetch returns a complete typed list
for a required destination/time boundary, or raises. It cannot silently
change credentials/region or present incomplete data as healthy empty data.
NC-492 owns mirror preservation, leg eligibility, cross-region completeness,
health and ticket generation, not this library.

## Proposed solution

Add the callable and row/exception exports to `voice_runtime.transports.twilio_call`:

```python
list_calls(*, api_host: str, account_sid: str, auth: tuple[str, str],
           to: str, start_after: datetime, page_size: int = 200,
           max_pages: int = 10) -> list[TwilioCallRow]
```

### Explicit configuration

- Use VR-004's shared explicit-client boundary: only `api.twilio.com` or
  `api.<edge>.<region>.twilio.com` with lowercase DNS labels; no scheme,
  port, userinfo, path/query or unrelated host. Nonblank strict ASCII
  alphanumeric account SID and exactly two nonblank auth strings. Account path
  identity is independent of the authenticating API key SID.
  Reject whitespace, control/non-ASCII characters, percent escapes, `/`,
  `\`, `?` and `#` before client construction, not merely before HTTP.
- `to` is required and nonblank; never read from the environment. Reject
  whitespace-only values; pass other values through as Twilio's filter.
- Require aware `start_after`, converted to UTC; naive raises before HTTP.
  `page_size` is integer 1..1000; `max_pages` a positive integer; reject bools.
- Invalid values raise `ValueError` (including Pydantic `ValidationError`);
  omitted keywords raise `TypeError`. Validate before HTTP, with no auth
  values in diagnostics.
- No credential/routing environment reads. Only `TWILIO_HTTP_TIMEOUT` is
  permitted: default 15 seconds, invalid/nonfinite/nonpositive overrides
  rejected. Clear per-client ambient region/edge overrides and bind the host;
  never mutate `os.environ`. Preserve lazy imports and VR-002's sole
  construction boundary in `_twilio_client.py`, shared with VR-004.

### Requests and complete pagination

First request: `GET https://<api_host>/2010-04-01/Accounts/<account_sid>/Calls.json`
with `To`, `StartTime>` and `PageSize`. Use SDK pages, not
`calls.list(limit=...)`; serialize the UTC datetime, not just its date.
Defensively exclude known timestamps at/before `start_after`: the consumer's
boundary is an instant, regardless of server-side filter precision.

Follow continuation state once per page, preserving its query/PageToken
without replacing it with the initial query parameters. Resolve relative
URLs against the explicit host. Before another request, require HTTPS,
identical host and exact account Calls collection path; reject changed
authority/account/path, userinfo, port, fragment or malformed continuation
with `ValueError`. Disable redirects; 3xx is failure, not an empty page.

No continuation means return all normalized rows, including `[]` on complete
empty success. Continuation remaining after `max_pages` raises
`TwilioCallListIncompleteError(RuntimeError)` with `api_host: str` and
`pages_fetched: int` attributes and a non-sensitive message. **No partial
list is returned.** Later-page HTTP/transport/parse/validation failure also
raises, discarding accumulated rows. No retry or silent partial result.

### Typed rows

`TwilioCallRow` is a frozen Pydantic model with exactly these public fields:

| Field | Type and normalization |
|---|---|
| `call_sid` | Nonblank strict string from `sid` |
| `status` | Nonblank strict string, verbatim |
| `start_time` | `float \| None`: UTC epoch seconds from an aware SDK datetime; null stays null, never zero |
| `direction` | Nonblank strict string, verbatim (`inbound`, `outbound-api`, etc.) |
| `caller` | `str \| None`, from SDK `from_`; null/blank becomes `None`; preserve other values, including anonymous labels |

Declare `pydantic>=2.0,<3` as a direct runtime dependency in `pyproject.toml`.
It is currently installed transitively through FastAPI, not declared directly;
the public row model makes it this package's own contract. VR-004 needs the
same declaration, added only once. No other dependency change is in scope.

Missing required strings, wrong types or malformed/non-aware timestamps
raise, not silently skip. Extra provider fields are not exposed. Missing/null
timestamp is explicitly unknown: retain with `start_time=None`, without
claiming it passed an exact-time filter. NC-492 must not treat unknown time
as an old eligible call. Direction filtering belongs to the consumer.

### Old callable and consumer seam

Keep `list_recent_calls` behavior from 0.1.13: implicit configuration, dict
shape and missing-credential empty result; add only a deprecation docstring.
Its deployed consumers must migrate explicitly. No delegation or removal.

NC-492 D-3's call signature works unchanged: omitted `max_pages` means 10.
The consumer must use typed fields, skip unknown-time rows for stub creation,
and treat the incomplete exception as a failed region. Fold these obligations
into NC-492 before consumer enforcement; this library FR does not authorize
CSAP edits. CSAP calls US1/IE1 separately using its required regional keys.

## Research and prior art

CSAP committed baseline: `7f0442bc8ccf2bbd54f76f691937b34ee88adc94`.
Sibling checkout required for cross-repo evidence; do not copy private
incident identifiers or secret fingerprints into this repository.

| Alternative / precedent | Observed evidence | Disposition |
|---|---|---|
| Widen old callable | Source uses `limit=200`, implicit destination and three-field dictionaries; NC-428 records consumer import drift | Reject in-place change; new explicit API with deliberate consumer migration |
| Return bounded partial lists | Existing cap is silent; NC-492 D-3a requires complete visibility | Named cap exception preserves the frozen list return type without false completeness |
| Copy NC-430 collector into CSAP | Collector separates auth/path SID, uses regional hosts and preserves continuation query by not reapplying params | Reuse wire semantics behind voice_runtime, not a consumer Twilio import |
| SMS single-active-region config | VBOT-97 Amendment 3 separates API-key auth/account SID and requires only active-region secrets | Reuse identity separation, not policy: NC-492 explicitly requires both regions |
| SDK kwargs / host override alone | Offline SDK 9.10.9: ambient Sydney/AU1 overrides Dublin base URL; clearing client overrides restores Dublin | Isolate explicit clients; assert final URLs under conflicting environment |

**Is this a graph?** No: deterministic provider pagination, no LLM stage.
Offline 2026-09-08 probe (fake HTTP, dummy credentials): SDK 9.10.9 emitted
parameter `StartTime>` with value `2026-09-08T06:30:00Z`, not a date-only
value. This says nothing about Twilio server precision, hence the defensive
timestamp filter. Earlier suites: 55 focused voice tests, 58 CSAP SMS/collector
tests passed; no live requests were made for this amendment.

## Acceptance criteria

- [ ] AC-01: Behavioral RED then GREEN: exact GET URL, separate account/auth,
      destination, UTC `StartTime>` and page size for both hosts; conflicting
      ambient credentials/routing cannot alter requests.
- [ ] AC-02: Missing keywords and invalid host/account/auth, blank destination,
      naive datetime and invalid page bounds fail before HTTP. No auth values
      in diagnostics and no unfiltered query.
  Table-driven account-SID cases reject non-ASCII/non-alphanumeric values,
  whitespace/control characters, percent escapes, `/`, `\`, `?` and `#`
  with `ValueError` before client construction and with zero HTTP requests.
- [ ] AC-03: Assert all five normalized fields, both directions and caller
      labels, aware-time epoch conversion, retained null time, exclusion at
      and before cutoff, and rejection of malformed rows.
- [ ] AC-04: Two-page success preserves PageToken/query; complete empty returns
      `[]`; cap with continuation raises the named exception without partial
      return. Cover `max_pages=1` and empty page with continuation.
- [ ] AC-05: Page-two failure and malformed payload raise without partial
      return. Host/account/path-changing continuation and redirects fail with
      no alternate-location request; relative and valid absolute links work.
- [ ] AC-06: Every page has default/overridden bounded HTTP timeout; invalid,
      nonfinite/nonpositive overrides fail before HTTP. No retries; at most
      `max_pages` requests, transport exceptions propagate.
- [ ] AC-07: Old callable tests pass unchanged, except added deprecation
      assertion. No SMS/outbound/signature changes. Full offline suite passes;
      tests carry `@pytest.mark.req("VR-006")`. Minimal API skeleton may
      precede RED so failure is behavioral, not collection/import failure.
- [ ] AC-08: Record VR-006 decisions, typed migration example and changelog;
  built-distribution smoke imports `list_calls`, `TwilioCallRow` and
  `TwilioCallListIncompleteError` without requiring VR-004 exports. Declare
  `pydantic>=2.0,<3` directly; verify these imports in a clean environment
  installed from the built distribution, not an editable checkout.
- [ ] AC-09: Coordinate publication as 0.1.14; it may include VR-004 only
  after separate judgement and enforcement. VR-006 tests/smoke do not
  import VR-004-owned exports or require hangup changes. Either FR may
  implement its needed shared helper first. Pins/rollout are CSAP work.

## Scope and decisions (2026-09-08)

Only call-read implementation, shared explicit-client boundary, small model
module if size limits require it, tests, docs and release metadata, plus the
direct Pydantic v2 dependency declaration. No other dependency changes.
VR-004 owns hangup's intentional 404 change; this FR changes no
other callable. No deployment, credential access, billable calls or induced
incidents. NC-492's mirror guard gates its consumer authentication change,
not library work or NC-493 rollout.

Round-1 identifier and independent-authority revisions are folded. The shared
dependency assumption was disproved: local Pydantic 2.13.4 / FastAPI 0.141.1
are installed, but 0.1.13 has no direct Pydantic declaration. The owner
authorized this revised plan and docs PR on 2026-09-08; dependency scope is
re-submitted to independent judgement. This PR changes documents only, not
runtime or manifests; implementation is a later task.

## Sources

- [VR-002](VR-002-twilio-http-timeout.md) and its judgement; [VR-004](VR-004-region-aware-hangup.md).
- [Call implementation](../voice_runtime/transports/twilio_call.py), [client boundary](../voice_runtime/transports/_twilio_client.py), [timeout tests](../tests/test_vr002_twilio_http_timeout.py).
- [NC-492](../../../customer-service-agent-platform/feature-requests/NC-492-reconciler-restoration.md), [NC-493](../../../customer-service-agent-platform/feature-requests/NC-493-region-aware-reaper-hangup.md).
- [NC-488 evidence](../../../customer-service-agent-platform/feature-requests/evidence/NC-488-reconciler-diagnosis.md), [collector](../../../customer-service-agent-platform/troubleshooting/gather_twilio.py), [SMS precedent](../../../customer-service-agent-platform/feature-requests/VBOT-97-twilio-sms-standalone-component.md).
- [Dependency manifest](../pyproject.toml) (0.1.13, direct runtime dependencies).
