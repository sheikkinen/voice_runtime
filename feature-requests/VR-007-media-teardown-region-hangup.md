# VR-007 — Region-aware REST hangup at media teardown

**Priority:** MEDIUM
**Type:** Bug fix (consumer-supplied regions for an existing call end)
**Status:** Draft (unjudged)
**Requested:** 2026-10-01
**Effort:** 0.5 day, plus the CSAP wiring and one TEST call
**Target release:** 0.1.15
**First consumer:** CSAP. The worker passes its existing NC-493 region tuple at app construction.
**Prior art:** VR-003 (REST-first call end), VR-004 (explicit-region `hangup_call`), VR-006; CSAP NC-488, NC-493.

## Glossary

- **REST-first call end (VR-003):** when the consumer asks to end a call, the media handler first completes the call through Twilio's REST API. Twilio then closes the media WebSocket from its side, which avoids Twilio error 31921. If that fails, the handler closes the WebSocket itself.
- **Explicit-region mode (VR-004):** `hangup_call(call_sid, regions=(...))`, where each `TwilioRegion` carries an API host, the account path SID and an API-key auth pair. Hosts are tried in order: 404/20404 moves to the next host, success or 400/21220 ends the sweep, and anything else propagates.
- **Ambient mode:** `hangup_call(call_sid)` with no regions. It authenticates with `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` and goes to whatever host the SDK's ambient region settings give.

## Problem

`register_voice_websocket` (`voice_runtime/transports/twilio_ws.py:77`) ends calls through `rest_hangup_first` (lines 135-166). That always uses ambient mode: it requires `TWILIO_AUTH_TOKEN` and calls `hangup_call(call_sid)`. VR-004 added explicit-region mode, but left this path on ambient mode and said so: "This FR does not claim API-key-only media teardown support."

For the first consumer, ambient mode cannot succeed. CSAP NC-488 recorded two independent reasons: the auth token deployed to the CSAP worker is rejected by `api.twilio.com` (401/20003), and the inbound call legs live in IE1, not US1. Field evidence, CSAP TEST Cloud Logging, 2026-09-17 to 2026-10-01: 16 REST-first attempts, 16 × `REST hangup failed (HTTP 401 error: … auth token is not valid for account …) — falling back to WS close`, 0 successes. The first was 2026-09-21. Each fallback is a server-side WebSocket close, which is the exact 31921 source VR-003 set out to remove.

The consumer already holds working regional API keys: CSAP's reaper ends calls with explicit-region mode (NC-493), using a tuple built by `services/supervisor_reaper.py:hangup_regions`.

## Change

1. `register_voice_websocket(app, session, *, hangup_regions: tuple[TwilioRegion, ...] | None = None)`. The new keyword-only argument defaults to `None`.
2. In `rest_hangup_first`:
   - **When `hangup_regions` is given:** call `hangup_call(call_sid, regions=hangup_regions)` in the existing worker thread. Do not require `TWILIO_ACCOUNT_SID` or `TWILIO_AUTH_TOKEN` for this step.
   - **When it is `None`:** keep today's behaviour byte for byte: the ambient-mode precheck and `hangup_call(call_sid)`.
   - Every exception, including `TwilioCallNotFoundError`, still falls back to the WebSocket close. The bounded wait for Twilio's close (`REST_CLOSE_WAIT_S`) is unchanged.
3. The tuple is validated once at registration (`TwilioRegion.model_validate` per element, nonempty, distinct hosts), so a bad tuple fails at startup, not mid-call. Credentials never appear in logs or exceptions (VR-004 rule).

**Not changed:**
- **Signature validation of the WebSocket upgrade** keeps using `TWILIO_AUTH_TOKEN`.
- **`hangup_call`, `TwilioRegion` and the client construction** stay as they are.
- **No new environment variables and no consumer env-name parsing.** As VR-004 ruled, the library never reads CSAP's key names, so the consumer builds the tuple.

## Consumer wiring (CSAP, separate change)

The consumer change is one line, in `server_fsm.py`, where the worker calls `register_voice_websocket`:

- pass `hangup_regions=hangup_regions(os.environ)` when `missing_hangup_keys(os.environ)` is empty;
- otherwise pass `None` and log the missing key names once.

It also raises the CSAP pin to `voice-runtime==0.1.15` (`requirements-deploy.txt`, `pyproject.toml`). This is tracked as its own CSAP FR, because it changes another repository's code and deployment. The TEST observation (AC-06) belongs to it.

## Acceptance criteria

- [ ] AC-01: A test-only RED commit adds `tests/test_vr007_media_teardown_regions.py` and fails on assertions.
- [ ] AC-02: With `hangup_regions` given and `TWILIO_AUTH_TOKEN` unset, a requested disconnect calls `hangup_call` once with `regions=` that tuple. When the fake Twilio then closes the WebSocket, no server-side `close(1000)` is sent. The test asserts on the fake transport, not on mocks of `rest_hangup_first`.
- [ ] AC-03: With `hangup_regions` given, a `TwilioCallNotFoundError`, an HTTP 401 error and a timeout each lead to exactly one server-side `close(1000)`.
- [ ] AC-04: With `hangup_regions=None`, the existing VR-003 and VR-004 media tests pass unchanged, which shows ambient mode is preserved.
- [ ] AC-05: Registering with an empty tuple, duplicate hosts or an invalid element raises `ValueError` at registration, and the message contains no auth value.
- [ ] AC-06: Owned by the CSAP FR. After the CSAP worker passes the tuple on TEST, one bot-ended call logs `Twilio closed the stream after REST hangup` and no `REST hangup failed` line in its window.
- [ ] AC-07: The full voice_runtime suite passes. 0.1.15 is published per `PUBLISHING.md`, and `CHANGELOG.md` records VR-007.

## Out of scope

- Fixing or rotating the CSAP worker's `TWILIO_AUTH_TOKEN` (an owner credential task; NC-488 D-A).
- Changing region order. The consumer supplies it; CSAP's reaper order is US1 then IE1.
- The reaper and CDR paths, which VR-004 and VR-006 already made region-aware.
