# Feature Request: VR-004 Region-aware Twilio REST — fix hangup on IE1-homed numbers

**Priority:** HIGH (Tervola prod line is IE1-homed; VR-003's fix is inert there)
**Type:** Bug fix
**Status:** Draft — **never implemented.** Targeted 0.1.13 (AC-06);
0.1.13 shipped VR-005 instead, and `hangup_call` still queries the
default host with the account credential and still treats a bare 404 as
success. Revived 2026-09-08 as a dependency of csap
`feature-requests/NC-493-region-aware-reaper-hangup.md`, which is its
consumer half; retarget the version bump in AC-06 accordingly, and
consider releasing alongside `VR-006-region-aware-cdr-read.md` so the
consumer pins once. The defect below is unchanged and was re-confirmed
live on 2026-09-08 (csap
`feature-requests/evidence/NC-488-reconciler-diagnosis.md`): the same
credential and the same region also break the consumer's forced hangup
on the TEST and STG numbers, not only the Tervola prod line.
**Effort:** 0.5 day
**Requested:** 2026-08-17
**Downstream:** csap NC-429 (AC-03 closed for us1 only), VBOT-88 correlator, Tervola release plan

## Defect (field evidence, 2026-08-17)

VR-003's REST-first teardown is broken for numbers whose calls are
processed in a non-default Twilio region. Confirmed topology (csap
`feature-requests/evidence/NC-429-zero-31921.md`): the Tervola prod line
(+358454913431) homes its inbound call resources in **IE1 (Dublin)**;
test/stg numbers home in us1.

Failure chain observed live:

1. `hangup_call(call_sid)` (`transports/twilio_call.py:114`) queries the
   **us1 default host** via `build_twilio_client` — an IE1-homed call
   returns **404/20404 while the call is LIVE**.
2. The VR-003 idempotency rule (`twilio_call.py:140` "Call already
   terminal") converts that 404 into silent success.
3. `watch_disconnect` waits 5 s for a Twilio-side close that never comes,
   falls back to server-side `close(1000)` → **error 31921** — exactly
   what VR-003 shipped to eliminate.

Two defects, both must be fixed:

- **D-1 (region routing):** REST calls must reach the region that owns
  the call resource.
- **D-2 (unsound idempotency):** 404/20404 is NOT proof of "already
  terminal" — it can mean "wrong region, call alive". Success may only
  be inferred from corroborated evidence.

## Fix contract

**D-1 — region-aware client:**

- New env contract: `TWILIO_REGION_CREDENTIALS` is NOT introduced;
  instead mirror the consumer's proven matrix (csap NC-430):
  `TWILIO_ACCOUNT_SID` + `TWILIO_AUTH_TOKEN` (us1 default),
  `TWILIO_US1_API_SID/SECRET` (preferred when set),
  `TWILIO_IE1_API_SID/SECRET` (enables IE1).
- `hangup_call(call_sid)` tries the configured regions in order
  (us1 first, then IE1) — a 404/20404 in one region falls through to the
  next; a 2xx terminates the search. Region list derives from configured
  credentials; SDK client per region via the existing
  `build_twilio_client` seam extended with host/region parameters
  (Twilio SDK: `region="ie1"`, `edge="dublin"`).
- No configured IE1 credentials → current behavior (us1 only), unchanged.

**D-2 — sound terminal detection:**

- 404/20404 from the LAST configured region → treated as **failure**
  (return falsy / raise the existing swallow path) so the caller's WS
  fallback fires immediately — never report success on absence of
  evidence.
- "Already terminal" success is only inferred from a **positive**
  signal: 2xx response, or an update rejected with Twilio error 21220
  (invalid state transition on a completed call).

## Acceptance Criteria

- [ ] AC-01: RED — mocked us1 404 + IE1 2xx: hangup succeeds via IE1;
      exactly one POST per region in order.
- [ ] AC-02: 404 in ALL configured regions → failure (no
      "already terminal" log at INFO; caller falls back to WS close).
- [ ] AC-03: 400+21220 in any region → success ("already terminal",
      caller hung up first).
- [ ] AC-04: No IE1 credentials → us1-only, one request, behavior
      byte-compatible with 0.1.12 for us1 successes.
- [ ] AC-05: VR-002 timeout discipline applies to every regional attempt.
- [ ] AC-06: CHANGELOG fragment; version bump (0.1.13).
- [ ] AC-07 (post-release, consumer): csap witness — one bot-ended call
      to the IE1-homed number via a current stack → zero 31921
      (gather_twilio window), recorded here and in csap NC-429 lineage.

## Constraints

1. `list_recent_calls`/`send_sms`/`initiate_outbound_call` are NOT in
   scope (separate FR if region-awareness is needed there; hangup is the
   call-path-critical one).
2. No new dependencies; Twilio SDK region/edge parameters only.
3. Never raise into the media path — the existing swallow contract in
   `watch_disconnect` stands.

## Related

- VR-003 (AC-09 partially verified — us1 only) + judgement
- csap `feature-requests/evidence/NC-429-zero-31921.md` (topology proof)
- csap `troubleshooting/gather_twilio.py` (NC-430) — consumer-side
  region matrix precedent and the AC-07 verification instrument
