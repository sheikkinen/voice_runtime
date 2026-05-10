# NC-284: Probe Twilio WebSocket Upgrade Headers

**Status:** Pending judgement
**Effort:** 1h
**Requested:** 2026-05-10
**Blocks:** NC-283 enforcement

## Problem

NC-283 (Twilio request signature validation) assumes that Twilio sends `X-Twilio-Signature`
on the WebSocket upgrade request for Media Streams. This is **not confirmed by Twilio's
documentation**. Twilio documents signature validation for HTTP webhooks; Media Streams
WebSocket connections originate from Twilio's infrastructure after TwiML execution and
may behave differently.

If the assumption is wrong, NC-283's implementation would reject all real Twilio connections.
Before writing a single line of production code, we need the empirical answer.

## Proposal

A one-shot probe script `scripts/probe_twilio_ws_headers.py` that:

1. Starts a FastAPI server with `/voice` WebSocket endpoint that captures all HTTP upgrade headers
2. Exposes it via ngrok
3. Places a real Twilio call using `initiate_outbound_call()` with TwiML pointing at the probe
4. Waits for the WebSocket upgrade to arrive
5. Dumps all headers to stdout and `/tmp/twilio_ws_headers.json`
6. Closes the connection immediately (code 1000) — no call flow required
7. Reports presence/absence of `X-Twilio-Signature` and its value if present

## Why a script, not a pytest test

- One-time empirical observation — not a repeatable behavioral assertion
- Requires live Twilio credentials + ngrok — not suitable for CI
- Answers a factual protocol question; the answer informs NC-283 design
- If present: its value becomes a fixture for NC-283 unit tests
- If absent: NC-283 pivots to validating on the TwiML HTTP endpoint instead

## Expected output

```
=== Twilio WebSocket Upgrade Headers ===
host: abc123.ngrok-free.app
upgrade: websocket
connection: Upgrade
x-twilio-signature: <value>   ← present or ABSENT
x-forwarded-for: ...
user-agent: TwilioProxy/1.1

RESULT: X-Twilio-Signature IS PRESENT
Saved to /tmp/twilio_ws_headers.json
```

## Acceptance Criteria

- [ ] `scripts/probe_twilio_ws_headers.py` exists and runs with credentials from `.env`
- [ ] Script starts server, starts ngrok, places call, captures headers, exits cleanly
- [ ] Output clearly states `X-Twilio-Signature IS PRESENT` or `IS ABSENT`
- [ ] Full header dict saved to `/tmp/twilio_ws_headers.json`
- [ ] Result documented in NC-283 judgement as Amendment 1 resolution

## Out of Scope

- Implementing the actual signature validation (NC-283)
- Any CI integration
- Handling the full call flow after header capture

## Implementation Notes

- Reuse ngrok startup pattern from `start-symptom-answerer.sh` (subprocess + admin API poll)
- Call own number so call completes TwiML quickly and WS upgrade follows immediately
- Single-use: run once, read the result, delete or archive the script

---

## Result (2026-05-10)

**Status:** DONE — probe executed, result confirmed.

### Twilio WebSocket Upgrade Headers (captured live)

```
host: capacitive-bernetta-transitorily.ngrok-free.dev
user-agent: Twilio.TmeWs/1.0
connection: Upgrade
sec-websocket-key: luxFfW4ekMivF8y4ajVwzw==
sec-websocket-version: 13
upgrade: websocket
x-forwarded-for: 3.83.109.252
x-forwarded-host: capacitive-bernetta-transitorily.ngrok-free.dev
x-forwarded-proto: https
x-twilio-signature: Oh4FvfCToOYm1cbtN5bfEsSoYxU=   ← PRESENT
accept-encoding: gzip
```

### ✅ X-Twilio-Signature IS PRESENT

Twilio sends `X-Twilio-Signature` on the HTTP upgrade request for Media Streams WebSocket
connections. The signature covers the `wss://` URL of the stream endpoint.

**Impact on NC-283:**
- Amendment 1: RESOLVED — signature validation is viable on WebSocket upgrade
- Use `Twilio.TmeWs/1.0` as `user-agent` in unit test fixtures for realistic mocking
- The example signature `Oh4FvfCToOYm1cbtN5bfEsSoYxU=` is URL+key specific; generate
  a valid test fixture using `RequestValidator(test_token).compute_signature(url, {})`

**NC-283 can proceed to enforcement.**
