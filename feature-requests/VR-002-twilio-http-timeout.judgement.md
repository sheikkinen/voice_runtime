# Judgement: VR-002 Bound the Twilio REST HTTP timeout

**Verdict:** APPROVED WITH REVISIONS — the boundary fix is real and minimal, but authority activates only after the FR folds in the required AC/test clarifications below.

**Reviewed against:** `projects/voice_runtime/feature-requests/VR-002-twilio-http-timeout.md`; cited GitHub issue `sheikkinen/voice_runtime#2`; `projects/voice_runtime/voice_runtime/transports/twilio_sms.py`; `projects/voice_runtime/voice_runtime/transports/twilio_call.py`; `projects/voice_runtime/tests/test_nc193_twilio_sms.py`; `projects/voice_runtime/tests/test_twilio_call.py`; prior-art FRs `projects/voice_runtime/feature-requests/NC-154-transport-intent-abstraction.md`, `NC-193-triage-result-delivery-sms-service.md`, `NC-285-route-token-stream-xml.md`, `NC-271-mock-transport-bridge.md`; judge doctrine `.github/skills/judge-fr/doctrine.md`; repo doctrine `.github/copilot-instructions.md`.

## What is sound

The problem is evidenced: `send_sms` constructs `Client(account_sid, auth_token)` directly after credential checks (`twilio_sms.py:22-30`), and `initiate_outbound_call`, `hangup_call`, and `list_recent_calls` do the same in `twilio_call.py:88-150`. The cited issue reports the same unbounded-SMS hazard and the consumer release dependency.

The proposed shape matches repo doctrine: normalize at the external boundary (`.github/copilot-instructions.md:51`), avoid partial remediation (`.github/copilot-instructions.md:76`), and prove the fix with RED tests first (`.github/copilot-instructions.md:220`). A single internal helper is the right minimum: prior art already treats `voice_runtime.transports.twilio_call` as the Twilio boundary (`NC-154:18-20`; `NC-285:17-27`), while NC-193 established runtime-owned SMS transport (`NC-193:46-52`).

Strategic classification: **contrib/example transport-boundary defect fix**, not a YAMLGraph framework primitive. It has concrete consumers and a real abstraction gap, but the authorized surface is the `voice_runtime` Twilio REST boundary only.

## Required revisions

### R-1: Make the direct-client construction test executable

Revise AC-3 so the static test forbids direct `twilio.rest.Client(...)` construction in transport modules **except inside the new helper module/function**. As written, “no module under `voice_runtime/transports/` constructs `twilio.rest.Client(...)` directly” (`VR-002:82-84`) would also forbid the helper proposed under `voice_runtime/transports/_twilio_client.py` (`VR-002:119-128`).

### R-2: Align timeout-propagation criteria with request-time failures

Revise AC-4 and the RED test list so timeout propagation is tested at the request method, not merely at client construction. The criterion says “underlying HTTP client raises a timeout” and names `send_sms` / `hangup_call` (`VR-002:87-89`), but the proposed test says “helper's client raises; assert it escapes `send_sms`” (`VR-002:112-113`). Require representative request-time exceptions from `messages.create(...)` and `calls(...).update(...)` to propagate unchanged.

### R-3: Preserve and state the existing credential/no-op split

Revise AC-5 to distinguish credential-validation errors from the existing CDR no-op behavior. `send_sms`, `initiate_outbound_call`, and `hangup_call` raise before constructing a client when required credentials are absent (`twilio_sms.py:24-30`; `twilio_call.py:90-104`; `twilio_call.py:125-129`), but `list_recent_calls` intentionally returns `[]` when credentials are absent (`twilio_call.py:144-150`). The FR must preserve both behaviors and state both mechanically.

### R-4: Add prior-art disposition to the FR

Add a short “Prior art” section before enforcement. It must disposition at least NC-154, NC-193, NC-285, and NC-271: NC-154/NC-285 support keeping Twilio-specific construction/wire details inside `voice_runtime`; NC-193 supports runtime-owned SMS delivery; NC-271 explicitly leaves real Twilio provider failure simulation out of mock transport scope (`NC-271:265-272`), supporting this FR's deterministic no-network unit-test approach.

## Scope is frozen

| Deliverable | Surface |
|---|---|
| D-1 | `projects/voice_runtime/voice_runtime/transports/_twilio_client.py` or equivalent internal helper |
| D-2 | `projects/voice_runtime/voice_runtime/transports/twilio_sms.py` |
| D-3 | `projects/voice_runtime/voice_runtime/transports/twilio_call.py` |
| D-4 | `projects/voice_runtime/tests/test_vr002_twilio_http_timeout.py` |
| D-5 | Minimal updates to existing Twilio transport tests only if needed to preserve current assertions |
| D-6 | `projects/voice_runtime/feature-requests/VR-002-twilio-http-timeout.md` revision status/decisions |
| D-7 | Voice-runtime changelog/release metadata required by that repo's release process |

Not authorized: retry/backoff, async wrapping, moving SMS off the call path, consumer repo pin bumps, Twilio WebSocket changes, `mock_bridge.py` timeout changes, public function signature changes, live Twilio/network tests, or broad transport refactors.

## Revised acceptance criteria

- [ ] AC-01: With `TWILIO_HTTP_TIMEOUT` unset, `send_sms`, `initiate_outbound_call`, `hangup_call`, and `list_recent_calls` construct the Twilio REST client through the shared helper with an HTTP client configured for `15.0` seconds; verified without network.
- [ ] AC-02: `TWILIO_HTTP_TIMEOUT=3` produces a `3.0` second HTTP timeout; invalid values raise during helper construction and do not fall back silently.
- [ ] AC-03: A static or behavioral test proves all Twilio REST call sites outside the shared helper route through the helper; the helper itself is the only authorized direct `Client(...)` construction point.
- [ ] AC-04: Request-time timeout exceptions from the SMS send and hangup REST calls propagate unchanged; no swallowing, fake success, or retry loop is introduced.
- [ ] AC-05: Existing credential behavior is preserved: SMS/outbound/hangup raise before constructing a client when required credentials are absent, and `list_recent_calls` still returns `[]` without constructing a client when credentials are absent.
- [ ] AC-06: Existing `tests/test_nc193_twilio_sms.py` and `tests/test_twilio_call.py` pass without weakening their assertions.
- [ ] AC-07: The implementation preserves lazy Twilio imports so `voice_runtime` remains importable without the optional Twilio SDK until a Twilio-backed function is called.

## Conditions for enforcement

| # | Condition | Severity |
|---|---|---|
| C-1 | Fold R-1 through R-4 into the FR before implementation authority is exercised. | GATE |
| C-2 | Add RED tests before production code; the tests must fail for the current direct `Client(...)` construction sites. | GATE |
| C-3 | Keep the change at the Twilio REST construction boundary; do not add retries, async execution, or consumer-level orchestration. | GATE |
| C-4 | Do not modify consumer repositories or pins under this FR; release/pin bumps require their own authority. | GATE |
| C-5 | Preserve optional-import behavior: no module-level import of `twilio.rest.Client` or `twilio.http.http_client.TwilioHttpClient`. | GATE |

Authority granted: after the required FR revisions are folded in, implement a single internal Twilio REST client helper with a configurable bounded HTTP timeout and migrate the four existing voice_runtime Twilio REST call sites to it.
