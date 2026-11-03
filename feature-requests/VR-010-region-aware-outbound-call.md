# VR-010 — Explicit-region Twilio outbound call

**Priority:** HIGH
**Type:** Feature (explicit provider contract, additive keyword argument)
**Status:** Implemented and committed (`f8529fd`, 2026-11-03); release 0.3.0 prepared on `chore/release-0.3.0`; **not judged**. The code came before this FR, which goes against the usual plan-judge-enforce order. The RED tests were witnessed failing before the change, see the [Implementation record](#implementation-record-2026-11-03). Next: judge, then commit RED and GREEN separately.
**Requested:** 2026-11-03
**Effort:** 0.25 day (library only)
**Target release:** next **minor** release after 0.2.0 (a new public keyword argument is a feature; `reference/release-checklist.md`). Bumped to **0.3.0** with CHANGELOG entry (AC-08 done).
**First consumer:** outcaller (`sheikkinen/outcaller-yamlgraph`) `nodes/twilio_call.py::_place_outbound_call`, which builds an IE1 `TwilioRegion` from `TWILIO_IE1_OUTGOING_API_SID` / `_SECRET` and `TWILIO_ACCOUNT_SID`.
**Prior art:** VR-004 (explicit-region hangup), VR-006 (explicit-region CDR read), VR-007 (media teardown REST hangup via `hangup_regions`); csap-black-box-tests VBOT-113 `_place_outbound_call`; [regional-twilio-migration.md](../docs/regional-twilio-migration.md).

## Terms

- **IE1:** Twilio's Ireland (Dublin) data-residency region. Its REST host is `api.dublin.ie1.twilio.com`.
- **API key pair:** a Twilio API Key SID (`SK…`) and secret. These are separate from the Account Auth Token and are valid only in the region where they were created.
- **Account Auth Token:** the account-level secret (`TWILIO_AUTH_TOKEN`). It has been recycled and is no longer usable for this account.
- **`TwilioRegion`:** voice_runtime's typed region identity: `api_host`, `account_sid` and `auth=(sid, secret)`. It was introduced by VR-004.

## Problem and ideal result

`initiate_outbound_call(phone)` reads `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` and uses the SDK's default host. Hangup (VR-004, VR-007) and the CDR read (VR-006) already accept an explicit `TwilioRegion`; placing the call is the last Twilio REST operation in voice_runtime without one.

Observed 2026-11-03:
- With the Auth Token, every outcaller call failed with 401 (Twilio error 20003).
- The IE1 API key pair returned 200 on `api.dublin.ie1.twilio.com/.../Calls.json` and 401 on `api.twilio.com`.

Consumers are left with two choices:
- **Use the dead token:** the call fails.
- **Re-implement call placement themselves**, as csap-black-box-tests did in VBOT-113. In outcaller this breaks the NC-154 gate (`tests/unit/test_no_twilio_deps.py`), because it needs the Twilio stream XML (TwiML) literal in consumer code.

**Ideal result:** consumers pass the same `TwilioRegion` they already use for hangup. voice_runtime places the call through that region's host and identity, and the stream XML stays inside voice_runtime.

## Proposed solution

Extend `voice_runtime.transports.twilio_call.initiate_outbound_call`:

```python
def initiate_outbound_call(phone: str, *, region: TwilioRegion | None = None) -> str
```

- **`region is None`:** unchanged. Requires `VOICE_STREAM_URL`, `TWILIO_ACCOUNT_SID` + `TWILIO_AUTH_TOKEN` and `TWILIO_PHONE_NUMBER`, and calls `build_twilio_client(account_sid, auth_token)`.
- **`region` given:**
  - `TwilioRegion.model_validate(region)` runs first, the same as `_validated_regions` in hangup. An invalid host raises `ValueError` ("Invalid Twilio API host").
  - `VOICE_STREAM_URL` and `TWILIO_PHONE_NUMBER` are still required (`RuntimeError`).
  - `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` are **not read**; the account and identity come from the region.
  - The client is `build_explicit_twilio_client(region)`. There is **no fallback** to the ambient token.
- The call request is the same in both modes: `calls.create(to=phone, from_=TWILIO_PHONE_NUMBER, twiml=build_stream_twiml(VOICE_STREAM_URL))`.

Single-region by design. Unlike hangup, placing a call is not idempotent: trying a second region after an ambiguous failure could ring the callee twice. A `regions` tuple is therefore out of scope.

**Is this a graph?** No. It is a deterministic HTTP transport with no LLM stage.

## Acceptance criteria

- **AC-01:** With `region`, the call uses `build_explicit_twilio_client(region)`, never `build_twilio_client`, and succeeds with `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` empty. Witness: `tests/test_twilio_call.py::TestInitiateOutboundCallRegion::test_region_uses_explicit_client_without_auth_token`.
- **AC-02:** In region mode, `calls.create` receives `to`, `from_=TWILIO_PHONE_NUMBER` and stream XML pointing at `wss://<host>/voice`. Witness: same test.
- **AC-03:** In region mode, a missing `VOICE_STREAM_URL` raises `RuntimeError` naming it. Witness: `test_region_still_requires_stream_url`.
- **AC-04:** In region mode, a missing `TWILIO_PHONE_NUMBER` raises `RuntimeError` naming it. Witness: `test_region_still_requires_phone_number`.
- **AC-05:** A region with an invalid host is rejected before any client is built (`ValueError`, "Invalid Twilio API host"). Witness: `test_region_is_revalidated`.
- **AC-06:** Without `region`, behaviour is unchanged. Witness: the existing `TestInitiateOutboundCall` (NC-155) tests pass unmodified.
- **AC-07:** The full voice_runtime suite is green.
- **AC-08:** Before release: a CHANGELOG entry, a minor version bump, and a docstring describing both modes (the docstring is done).

## Scope

- **In:** the `region` keyword argument, its tests, the docstring, and the release items in AC-08.
- **Out:**
  - multi-region retry for call placement (see above);
  - changing `_get_twilio_env`;
  - removing ambient-token mode (consumers still use it: CSAP and older outcaller);
  - consumer wiring (outcaller and csap-black-box-tests own their region construction).

## Implementation record (2026-11-03)

- **RED:** 4 new tests in `tests/test_twilio_call.py::TestInitiateOutboundCallRegion` failed with `TypeError: initiate_outbound_call() got an unexpected keyword argument 'region'` (4 failed, 14 passed).
- **GREEN:** `voice_runtime/transports/twilio_call.py::initiate_outbound_call` gained the keyword-only `region` argument as specified. Full suite: 640 passed, 1 skipped. `ruff format --check` is clean for both files. The remaining `ruff check` findings (S101, SIM117) are the existing style of the test file.
- **Field evidence:** outcaller placed call `CA5efd11ed916efcb1b0b4e6813df80c32` through `initiate_outbound_call(phone, region=<IE1>)`. Twilio replied 201 on `api.dublin.ie1.twilio.com/.../Calls.json`. The call completed after 123 s with a full probe, extract and recap conversation.
- **Deviation:** implemented before this FR was written and judged. Committed as a single commit `f8529fd` (RED and GREEN not split). CHANGELOG and version bump (AC-08) done for 0.3.0.

## Source references

- `voice_runtime/transports/twilio_call.py:87`: `initiate_outbound_call`
- `voice_runtime/transports/_twilio_models.py:17`: `TwilioRegion`
- `voice_runtime/transports/_twilio_client.py:32`: `build_explicit_twilio_client`
- `tests/test_twilio_call.py:179`: `TestInitiateOutboundCallRegion`
