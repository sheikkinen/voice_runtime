# Feature Request: VR-006 Region-aware, explicitly-filtered CDR read

**Priority:** HIGH (the consumer's record reconciler has produced nothing
since it was written; this callable is why)
**Type:** Bug fix + contract change
**Status:** Draft
**Effort:** 0.5 day
**Requested:** 2026-09-08
**Consumer:** csap `feature-requests/NC-492-reconciler-restoration.md`
(D-3, "contract frozen") — that FR names this release as an
unconditional deliverable, not a contingency
**Sibling:** `VR-004-region-aware-hangup.md` — the same defect on the
call-teardown path. VR-004's Constraint 1 deliberately excluded
`list_recent_calls` and said "separate FR if region-awareness is needed
there". It is needed there. **This is that FR.**

## Defect

`list_recent_calls(lookback_s)` (`transports/twilio_call.py`, shipped
0.1.10 for NC-395/NC-428) cannot support a correct reconciler, for four
independent reasons:

1. **Single region.** It builds a default-host client, so it reads us1
   only. The consumer's inbound call resources are homed in IE1 — the
   topology VR-004 already documents. The rows it returns are therefore
   the wrong legs (`outbound-api`), whose SIDs never match the consumer's
   records, which are keyed by the inbound leg.
2. **Implicit destination.** The filter comes from `TWILIO_PHONE_NUMBER`
   in the environment. Where that variable is absent — as it is in the
   consumer's deployment, moved out by an unrelated refactor — the query
   is issued as `To=None`, i.e. account-wide across every number and
   every tier. Observed live 2026-09-08.
3. **Lossy row shape.** It returns `{call_sid, status, start_time}`. The
   consumer needs `direction`, to know which leg it is looking at, and
   the caller's number, so a stub built from a row is actionable rather
   than anonymous. Twilio supplies both; this function discards them.
4. **Unbounded paging.** `PageSize=200` with SDK auto-pagination and no
   cap, and no way for a caller to learn that results were truncated.

## Fix contract

Add a new callable rather than widening the old one — the old signature
has a deployed consumer and an env-implicit destination that must not
survive:

```
list_calls(*, api_host: str, account_sid: str, auth: tuple[str, str],
           to: str, start_after: datetime, page_size: int = 200,
           max_pages: int = 10) -> list[TwilioCallRow]
```

- **`api_host`** — the caller chooses the region: `api.twilio.com`, or
  `api.<edge>.<region>.twilio.com` for a Data Residency region. No
  default; the caller is explicit or it does not compile.
- **`account_sid`** — the **account path** segment of
  `/2010-04-01/Accounts/<account_sid>/Calls.json`. This is not
  authentication identity: an API key SID authenticates but does not
  name the account. Blank raises before any request is issued.
- **`auth`** — `(username, password)`. Per-region API key pairs are the
  intended use; the account SID + auth token pair still works where a
  deployment has nothing else.
- **`to`** — the destination filter, explicit and required. **Blank
  raises before any request is issued.** No environment fallback: an
  unfiltered account-wide CDR query must be unreachable, not merely
  discouraged.
- **`start_after`** — a timezone-aware `datetime`. Naive raises.
- **`max_pages`** — a validated bound. If provider continuation state
  remains when the cap is reached, the result is **incomplete** and the
  caller is told so rather than handed a silently truncated list.

`TwilioCallRow` is a typed row carrying at least `call_sid`, `status`,
`start_time`, `direction` and `caller`. Consumers stop normalizing
untyped dictionaries at their own boundary.

`list_recent_calls` is retained, unchanged, and marked deprecated in the
docstring with a pointer here. Removing it is a separate decision for a
later major.

## Acceptance Criteria

- [ ] AC-01: RED — `list_calls` issues exactly one request per page to
      `https://<api_host>/2010-04-01/Accounts/<account_sid>/Calls.json`
      with `To`, `StartTime>` and `PageSize` as query parameters.
- [ ] AC-02: Blank or missing `to` raises before any HTTP request;
      likewise blank `account_sid`; likewise a naive `start_after`. A
      test asserts no request was issued in each case.
- [ ] AC-03: Rows carry `call_sid`, `status`, `start_time`, `direction`
      and `caller`, typed, with `direction` preserved verbatim from the
      provider (`inbound`, `outbound-api`, …).
- [ ] AC-04: Both regional hosts are exercised against a mocked
      provider; the account path is the account SID in both, including
      when the authenticating credential is an API key SID.
- [ ] AC-05: Pagination stops at `max_pages`; continuation state
      remaining at the cap is reported as incomplete, not dropped. An
      invalid `max_pages` raises.
- [ ] AC-06: VR-002's timeout discipline applies to every request.
- [ ] AC-07: `list_recent_calls` behaviour is byte-compatible with
      0.1.13 and carries a deprecation note pointing here.
- [ ] AC-08: CHANGELOG fragment and version bump. If VR-004 releases in
      the same version, both callables and both contract changes are
      named in the entry — the consumer pins once and gets both.

## Constraints

1. No new dependencies; the Twilio SDK's own region/edge parameters or a
   plain host override, nothing more.
2. `send_sms` and `initiate_outbound_call` are out of scope. `hangup_call`
   is VR-004's.
3. No credential is read from the environment inside `list_calls`. Every
   input is a parameter — that is the point of the contract.
4. The consumer pins an exact version; this release must not change any
   other callable's behaviour.

## Related

- `VR-004-region-aware-hangup.md` — same defect, teardown path; its
  Constraint 1 is why this FR exists separately
- `VR-002-twilio-http-timeout.md` — the timeout discipline AC-06 inherits
- csap `feature-requests/NC-492-reconciler-restoration.md` — the consumer
  and its frozen expectations
- csap `feature-requests/evidence/NC-488-reconciler-diagnosis.md` — the
  live observations behind defects 1–4
- csap `feature-requests/NC-428-reconciler-voice-runtime-import-drift.md`
  — why the release and the consumer pin move together, declared rather
  than discovered
