# VR-007 — Region-aware REST hangup at media teardown

**Priority:** MEDIUM
**Type:** Bug fix (consumer-supplied regions for an existing call end)
**Status:** Implemented (2026-10-01); release 0.1.15 prepared locally, publication awaits the R-4 decision
**Requested:** 2026-10-01
**Effort:** 0.5 day (library only; CSAP wiring is a separate FR)
**Target release:** 0.1.15
**First consumer:** CSAP. The worker passes its existing NC-493 region tuple at app construction.
**Prior art:** VR-002 (bounded Twilio HTTP timeout), VR-003 (REST-first call end), VR-004 (explicit-region `hangup_call`), VR-006; CSAP NC-488, NC-493.
**Research:** [Dispositioned alternatives](#research-and-prior-art)
**Judgement:** [VR-007-media-teardown-region-hangup.judgement.md](VR-007-media-teardown-region-hangup.judgement.md)

## Glossary

- **REST-first call end (VR-003):** when the consumer asks to end a call, the media handler first completes the call through Twilio's REST API. Twilio then closes the media WebSocket from its side, which avoids Twilio error 31921. If that fails, the handler closes the WebSocket itself.
- **Explicit-region mode (VR-004):** `hangup_call(call_sid, regions=(...))`, where each `TwilioRegion` carries an API host, the account path SID and an API-key auth pair. Hosts are tried in order: 404/20404 moves to the next host, success or 400/21220 ends the sweep, and anything else propagates.
- **Ambient mode:** `hangup_call(call_sid)` with no regions. It authenticates with `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` and goes to whatever host the SDK's ambient region settings give.

## Problem and ideal result

`register_voice_websocket` (`voice_runtime/transports/twilio_ws.py:77`) ends calls through `rest_hangup_first` (lines 135-166). That always uses ambient mode: it requires `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` (line 146) and calls `hangup_call(call_sid)` (line 150). VR-004 added explicit-region mode, but left this path on ambient mode and said so: "This FR does not claim API-key-only media teardown support" (VR-004, line 86).

For the first consumer, ambient mode cannot succeed. CSAP NC-488 recorded two reasons: the auth token deployed to the CSAP worker is rejected by `api.twilio.com` (401/20003), and the inbound call legs live in IE1, not US1. The 2026-10-01 probe (see Research) suggests these are **one cause, not two**: the auth token in the local CSAP `.env` authenticates on `api.dublin.ie1.twilio.com` (account GET 200) and fails only on US1 with the same 401/20003. Twilio auth tokens are region-scoped, so ambient mode sends a valid IE1 token to the wrong region. The deployed TEST secret was not probed; it is assumed to be the same token, because the field error text matches. Field evidence, CSAP TEST Cloud Logging, 2026-09-17 to 2026-10-01: 16 REST-first attempts, 16 × `REST hangup failed (HTTP 401 error: … auth token is not valid for account …) — falling back to WS close`, 0 successes. The first was 2026-09-21. Each fallback is a server-side WebSocket close, which is the exact 31921 source VR-003 set out to remove.

The consumer already builds an explicit-region tuple for its reaper (NC-493, `services/supervisor_reaper.py:hangup_regions`, line 50).

**Ideal result:** every bot-ended call on a consumer with regional API keys ends through REST in the call's own region, and Twilio closes the stream. No 31921 error, and no auth token is needed for the hangup. Consumers without a tuple see no change.

## Proposed solution

1. `register_voice_websocket(app, session, *, hangup_regions: tuple[TwilioRegion, ...] | None = None)`. The new keyword-only argument defaults to `None`.
2. In `rest_hangup_first`:
   - **When `hangup_regions` is given:** call `hangup_call(call_sid, regions=hangup_regions)` in the existing `asyncio.to_thread` worker. Do not require `TWILIO_ACCOUNT_SID` or `TWILIO_AUTH_TOKEN` for this step.
   - **When it is `None`:** keep today's behaviour byte for byte: the ambient-mode precheck and `hangup_call(call_sid)`.
   - Every exception, including `TwilioCallNotFoundError`, still falls back to the WebSocket close. The bounded wait for Twilio's close (`REST_CLOSE_WAIT_S`) is unchanged.
3. **One validator (R-2).** The tuple checks inside `hangup_call` (`twilio_call.py:131-137`) move into one private helper in `twilio_call.py`. It accepts only a nonempty tuple, revalidates every element with `TwilioRegion.model_validate`, rejects duplicate hosts, and returns the validated `tuple[TwilioRegion, ...]`. `register_voice_websocket` calls it synchronously at registration and keeps the returned tuple; `hangup_call` uses the same helper and its returned tuple. A bad tuple fails at startup, before route use or client construction, and no copy of the checks is created. Credentials never appear in logs or exceptions (VR-004 rule; `TwilioRegion` already sets `hide_input_in_errors=True` and `repr=False` on `auth`).

**Not changed:**
- **Signature validation of the WebSocket upgrade** keeps using `TWILIO_AUTH_TOKEN`.
- **`hangup_call`'s routing behaviour, `TwilioRegion` and the client construction** stay as they are. Only the tuple validation moves into the helper.
- **No new environment variables and no consumer env-name parsing.** As VR-004 ruled, the library never reads CSAP's key names, so the consumer builds the tuple.

### Latency bound

Each regional client carries the VR-002 HTTP timeout (`TWILIO_HTTP_TIMEOUT`, default 15 s). With N regions, the worst case before the fallback close is N × that timeout, plus `REST_CLOSE_WAIT_S` (5 s) if the REST call succeeds but Twilio never closes the stream. For CSAP (N = 2, US1 then IE1), the worst case is 30 s, then 5 s. This is accepted: the disconnect is requested after the bot's final utterance, so the delay is silence on an open line before the hangup, not lost speech. An IE1 call adds one US1 404/20404 round trip before the IE1 request succeeds. Reordering is the consumer's choice (see Out of scope).

## Research and prior art

| Alternative | Probe / evidence | Disposition |
|---|---|---|
| Ambient mode with `TWILIO_REGION=ie1` / `TWILIO_EDGE=dublin` on the worker (no code change) | Probe 2026-10-01: the local token returns 200 on IE1, so this would probably end IE1 calls. But one ambient region cannot reach US1 legs (CSAP sweeps US1 then IE1), and the account token would stay on the call-end path, against NC-493 D-4 (the token stays at the webhook boundary). | Rejected. It covers one region and keeps the account token on the call-end path. |
| Fix or rotate the worker's `TWILIO_AUTH_TOKEN` | The probe shows the token is valid for IE1, so rotating it is not a fix. It also leaves the token on the call-end path. | Rejected. The local token contradicts the NC-488 D-A premise that the token is bad; the deployed secret is unprobed. |
| Library reads CSAP's regional key env names | Rejected by VR-004: the library must not parse consumer env names. | Rejected (precedent). |
| Pass `hangup_regions` at registration (this FR) | Reuses the VR-004 explicit-region sweep that CSAP already uses for its reaper. | Chosen. |

**Is this a graph?** No. This is deterministic provider transport and WebSocket teardown with no LLM stage.

| Precedent | What it supplies to VR-007 |
|---|---|
| VR-002 | The per-request HTTP timeout and the single client-construction boundary (`_twilio_client.py`). Unchanged. |
| VR-003 | REST-first teardown, off-loop execution (`asyncio.to_thread`), the bounded wait for Twilio's close, and the WS fallback. VR-007 only changes which `hangup_call` mode is selected. |
| VR-004 | Explicit-region routing, the 404/20404 vs 400/21220 classification, and the media-teardown carve-out that VR-007 closes. |
| VR-006 | Adjacent regional-provider work on CDR reads, not call teardown. No dependency. |
| CSAP NC-488 | Incident context. Its token diagnosis is narrowed by the 2026-10-01 probe (local token valid on IE1). |
| CSAP NC-493 | The existing consumer tuple builder. It grants no media-route wiring authority; that is the separate CSAP FR. |

**Premise evidence: CSAP regional keys (probed 2026-10-01 from the CSAP `.env`, run from a local machine)**

Read-only probe: `POST Calls/CA000…000.json Status=completed` on each host (no real call touched), plus `GET Accounts/{AC}.json`:

| Credential | Host | Account GET | Hangup of a nonexistent call |
|---|---|---|---|
| US1 API key | api.twilio.com | 401/70004 | **404/20404** |
| IE1 API key | api.dublin.ie1.twilio.com | 401/70004 | **404/20404** |
| Auth token | api.twilio.com | 401/20003 | 401/20003 |
| Auth token | api.dublin.ie1.twilio.com | 200 | 404/20404 |

Both API keys authenticate on the Calls resource. The account GET returning 401/70004 means only that standard API keys are not allowed to read the Account resource. It is not an auth failure. The US1 key classifies an absent call as 404/20404, so the sweep moves on to IE1 instead of propagating a 401.

Live probe: one call created on IE1 to the operator's phone (`CA428c27d5fe4b888252ef9908466bc641`), answered (`in-progress`), then ended by `hangup_call(sid, regions=hangup_regions(env))` in CSAP's US1→IE1 order. The function returned in 0.76 s with no exception, the call status was then `completed`, and the operator confirmed the call connected. This exercises the exact library function and the exact consumer tuple builder that this FR wires into media teardown. Not exercised: the media-WebSocket path itself (AC-02), and the deployed TEST worker's secrets (the keys were added to the local `.env` on 2026-10-01; whether TEST holds the same values is the CSAP FR's field acceptance).

## Acceptance criteria

Adopted verbatim from the judgement's revised criteria (R-2, R-3, R-4).

- [x] AC-01: A test-only behavioral RED commit adds `tests/test_vr007_media_teardown_regions.py`; failures are assertions against current behavior, not import, collection, or fixture errors. Every new or changed behavioral test carries `@pytest.mark.req("VR-007")`.
- [x] AC-02: The shared private validator returns a fully revalidated nonempty `tuple[TwilioRegion, ...]` with distinct hosts. Registration invokes it synchronously and retains that returned tuple. Empty tuple, list, duplicate hosts, later invalid object, and invalid `model_construct` instance each raise `ValueError` before route use/client construction, with no auth value in the exception.
- [x] AC-03: With validated `hangup_regions` supplied and both `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` unset, a requested disconnect invokes `hangup_call` exactly once through the existing off-loop worker with `regions=<validated tuple>`. When the fake Twilio transport then disconnects, no server-side `close(1000)` is emitted.
- [x] AC-04: With validated `hangup_regions` supplied, each of `TwilioCallNotFoundError`, `TwilioRestException(status=401, code=20003)`, and `requests.exceptions.ReadTimeout` from `hangup_call` produces exactly one server-side `close(1000)`, no retry, and no credential material in logs.
- [x] AC-05: With validated `hangup_regions` supplied and REST success but no Twilio-side disconnect before `REST_CLOSE_WAIT_S`, the existing bounded wait ends in exactly one server-side `close(1000)`.
- [x] AC-06: With `hangup_regions=None`, missing ambient credentials cause zero REST calls and exactly one server-side close; ambient credentials invoke `hangup_call(call_sid)` without `regions`; REST success followed by Twilio-side disconnect causes no server-side close; and ambient REST failure causes exactly one fallback close.
- [x] AC-07: `hangup_call` uses the shared validator while retaining VR-004 behavior: complete validation precedes client construction, 404/20404 alone advances the region sweep, 2xx or 400/21220 stops successfully, all-region absence raises `TwilioCallNotFoundError`, and any other provider/transport error propagates without a later attempt. Existing VR-004 focused tests pass without weakened assertions.
- [x] AC-08: WebSocket upgrade signature validation continues to use `TWILIO_AUTH_TOKEN`; supplying `hangup_regions` neither bypasses nor replaces that separate boundary. No new environment variables or consumer-specific key names are introduced.
- [x] AC-09: The full offline voice-runtime suite passes. Version metadata is `0.1.15`, `CHANGELOG.md` records VR-007, the sdist and wheel build successfully, `twine check` passes, and a clean non-editable wheel installation imports and exercises the changed registration API. No upload or tag operation is part of this criterion.
- [x] AC-10: The FR records implementation decisions, RED/GREEN witnesses, focused and full-suite results, artifact inspection, and any deviation. It keeps consumer wiring and field acceptance explicitly assigned to a separate CSAP FR.

**Release decision (R-4), open:** After implementation review and artifact inspection, do the required parties approve publishing and tagging voice-runtime 0.1.15? TestPyPI/PyPI upload and tag creation/push require that affirmative human decision; this FR's authority does not include them.

## Implementation record (2026-10-01)

**Commits:** RED `c4f2232` (test only), GREEN `6c39bb0`, then this record together with the release prep.

**RED witness:** 15 tests failed, all on assertions: `register_voice_websocket` had no `hangup_regions` parameter, and `twilio_call` had no `_validated_regions` helper. There were no import or collection errors.

**GREEN:** `twilio_call._validated_regions` is the one validator. `hangup_call` and `register_voice_websocket` both call it, and registration keeps the returned tuple. With regions supplied, `rest_hangup_first` skips the ambient-credential precheck and calls `hangup_call(sid, regions=...)` in `asyncio.to_thread`. With `None`, the code path is the old one. No environment reads were added; the only `getenv` lines in the diff are the existing ambient precheck, moved into the `None` branch.

**Witness map** (`tests/test_vr007_media_teardown_regions.py`; every test carries `req("VR-007")`):

| AC | Test |
|---|---|
| AC-02 | `TestRegistrationValidation::test_bad_tuple_rejected_before_route_use` (empty tuple, list, duplicate hosts, later invalid object, invalid `model_construct`) and `test_registration_and_hangup_call_share_one_validator` |
| AC-03 | `TestExplicitRegionTeardown::test_regions_without_ambient_creds_rest_first` |
| AC-04 | `TestExplicitRegionTeardown::test_rest_failure_falls_back_once` (one case per error class, with a log check for credentials) |
| AC-05 | `TestExplicitRegionTeardown::test_no_twilio_close_bounded_wait_then_close` |
| AC-06 | `TestAmbientModePreserved` (3 tests) |
| AC-07 | `test_registration_and_hangup_call_share_one_validator`, plus `tests/test_vr004_region_hangup.py` unchanged and green |
| AC-08 | `TestSignatureBoundaryUnchanged::test_bad_signature_rejected_with_regions` |

**Suites:** focused 15 passed; full `pytest tests/` 604 passed, 1 skipped.

**Artifact inspection (AC-09):** `pyproject.toml` is at 0.1.15, and `CHANGELOG.md` has a 0.1.15 VR-007 entry. `python -m build` produced the sdist and wheel. `twine check` passed on both; twine ran in a throwaway venv, and nothing was uploaded. A clean non-editable install of the wheel in a fresh venv imported the package, showed `hangup_regions` as keyword-only with a `None` default, and raised `ValueError("regions must be a nonempty tuple")` for `hangup_regions=()` at registration.

**Deviations:**
- The RED test's synchronous registration cases first used the VR-003 session factory, which needs a running event loop. GREEN switched them to a loop-free `VoiceSession`. The assertions did not change.
- A bare `pytest` at the repo root also collects a stale VR-004 worktree under `tmp/worktrees/`, which fails with a conftest path mismatch. The suite of record is `pytest tests/`. The stale worktree is untracked local state and was left in place.

**Not done under this authority:** TestPyPI/PyPI upload, tag creation or push (R-4 decision open), and CSAP wiring and field acceptance (separate CSAP FR).

## Consumer wiring (CSAP, separate change; does not gate this FR)

The consumer change is one call site, in `server_fsm.py:232`, where the worker calls `register_voice_websocket(app, session)`:

- pass `hangup_regions=hangup_regions(os.environ)` when `missing_hangup_keys(os.environ)` is empty (`services/supervisor_reaper.py:45`);
- otherwise pass `None` and log the missing key names once.

It also raises the CSAP pin to `voice-runtime==0.1.15` (`requirements-deploy.txt:5`, `pyproject.toml:22`). This is tracked as its own CSAP FR, because it changes another repository's code and deployment. That FR owns the field acceptance: after the CSAP worker passes the tuple on TEST, one bot-ended call logs `Twilio closed the stream after REST hangup` and no `REST hangup failed` line in its window.

## Out of scope

- Changing the CSAP worker's `TWILIO_AUTH_TOKEN`. The local token is valid for IE1; NC-488 D-A should be re-examined against the deployed secret.
- Changing region order. The consumer supplies it; CSAP's reaper order is US1 then IE1.
- The reaper and CDR paths, which VR-004 and VR-006 already made region-aware.

## Source references

- `voice_runtime/transports/twilio_ws.py:35` — `REST_CLOSE_WAIT_S = 5.0`
- `voice_runtime/transports/twilio_ws.py:77` — `register_voice_websocket`
- `voice_runtime/transports/twilio_ws.py:135-166` — `rest_hangup_first`
- `voice_runtime/transports/twilio_ws.py:169-178` — `watch_disconnect`, fallback `close(1000)`
- `voice_runtime/transports/twilio_call.py:123-151` — `hangup_call`, explicit and ambient modes
- `voice_runtime/transports/twilio_call.py:154-173` — `_complete_call`, 404/20404 and 400/21220 classification
- `voice_runtime/transports/_twilio_models.py:17-29` — `TwilioRegion`
- `voice_runtime/transports/_twilio_client.py:16` — `DEFAULT_TIMEOUT_S = 15.0`
- `feature-requests/VR-004-region-aware-hangup.md:86` — media-teardown carve-out
- CSAP `services/supervisor_reaper.py:45,50`, `server_fsm.py:232`, `requirements-deploy.txt:5`, `pyproject.toml:22`
