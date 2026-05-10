# Judgement: NC-283 Twilio Request Signature Validation

**Status:** APPROVED WITH AMENDMENTS
**Date:** 2026-05-10

---

## Verdict

APPROVE. The security gap is real and the fix is correct in intent. Three amendments required before enforcement.

---

## Affirmed

- **Problem is real**: `register_voice_websocket()` accepts all connections without auth. Confirmed in code.
- **Twilio dependency already present**: `twilio>=9.0.0` in `pyproject.toml`. `RequestValidator` costs nothing to add.
- **Scope is correct**: WebSocket endpoint only. HTTP TwiML endpoint deferred.
- **MockBridge unaffected**: confirmed — it never calls the FastAPI WebSocket handler.
- **`0.0.0.0` is a separate, lower-risk concern**: analysis confirmed ninchat_voice is correctly proxied by fly.io; local dev risk is bounded by ngrok URL opacity.

---

## Amendment 1 — The WebSocket upgrade signature question is unresolved

**Risk:** The FR assumes Twilio sends `X-Twilio-Signature` on the WebSocket upgrade request. This is **not documented behavior** in Twilio's Media Streams specification. Twilio signs HTTP requests (TwiML webhooks, status callbacks) but the Media Streams WebSocket connection originates from Twilio's infrastructure after TwiML execution — it may not carry the same signature header.

**Required before enforcement:** Write a test that captures real headers from a live Twilio WebSocket connection and confirms `X-Twilio-Signature` is present. If absent, the proposed implementation rejects all real Twilio connections.

**Fallback approach if signature absent:** Validate on the TwiML HTTP endpoint instead (the `<Connect>/<Stream>` response endpoint), which is a standard HTTP POST that Twilio does sign. This is out of scope per the FR but becomes in-scope if the WS header assumption fails.

---

## Amendment 2 — URL construction is fragile

The proposal builds the validation URL as:
```python
url = f"{VOICE_STREAM_URL}/voice"
```

But `VOICE_STREAM_URL` is an `https://` URL, while Twilio's WebSocket connect uses `wss://` (see `build_stream_twiml`). The URL Twilio signs is the `wss://` URL given in the TwiML `<Stream url="...">`. If `RequestValidator` is called with the `https://` version while Twilio signed the `wss://` version, validation always fails.

**Required:** Use `wss://` URL for validation, matching what is in the TwiML:
```python
ws_url = VOICE_STREAM_URL.replace("https://", "wss://").replace("http://", "ws://")
url = f"{ws_url}/voice"
```

---

## Amendment 3 — `TWILIO_SKIP_SIGNATURE_VALIDATION` bypass must be test-only

The FR says "test-only, documented" but this must be enforced, not advisory. The bypass env var must:
- Be checked at startup and logged as a `WARNING` if active (never silent)
- Never activate in an environment where `TWILIO_AUTH_TOKEN` is a real production token
- Be documented in `.env.example` with a comment: `# NEVER set in production`

The current FR leaves this as a documentation concern. Treat it as a code gate.

---

## Scope confirmed

**In:** Signature validation on `/voice` WebSocket endpoint, bypass var with warning, two unit tests  
**Out:** TwiML HTTP endpoint validation, rate limiting, mTLS  

---

## Acceptance Criteria (amended)

- [ ] Capture and confirm `X-Twilio-Signature` header presence in live Twilio WS upgrade (test or docs evidence)
- [ ] `register_voice_websocket()` validates signature using `wss://` URL before `accept()`
- [ ] Connections with missing/invalid signature receive close code 1008 + warning log
- [ ] Valid Twilio connections continue to work (live call smoke test)
- [ ] Unit test: invalid signature → close(1008), `accept()` not called
- [ ] Unit test: valid signature → `accept()` called
- [ ] `TWILIO_SKIP_SIGNATURE_VALIDATION=1` logs `WARNING` at startup; documented as test-only
- [ ] `.env.example` updated with bypass var + `# NEVER set in production` comment
