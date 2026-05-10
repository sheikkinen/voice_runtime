# NC-283: Twilio Request Signature Validation on WebSocket Endpoint

**Status:** Pending judgement
**Effort:** 0.5 day
**Requested:** 2026-05-10

## Problem

`twilio_ws.py` accepts any WebSocket connection at `/voice` without authentication:

```python
@app.websocket("/voice")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()  # no validation
```

Twilio signs every outbound request with `X-Twilio-Signature` — an HMAC-SHA1 of the full URL
concatenated with sorted POST params, signed with the account's Auth Token. Without validating
this signature, anyone who discovers the server URL (e.g., via ngrok URL leak or logs) can:

- Inject arbitrary audio into an active call
- Eavesdrop on call audio flowing through the session
- Trigger call setup logic against any target number
- Exhaust Twilio quotas via spoofed requests

Twilio's own documentation marks signature validation as **required for production**. This is a
known OWASP A01 (Broken Access Control) pattern.

## Constraint

WebSocket upgrades carry headers but not POST bodies. Twilio sends the `X-Twilio-Signature`
header on the HTTP upgrade request before the WebSocket handshake completes. FastAPI's
`WebSocket` object exposes `websocket.headers` before `await websocket.accept()`.

`twilio.request_validator.RequestValidator` validates signatures using the full URL and an
optional dict of POST params (empty for WebSocket upgrades).

## Proposal

Add signature validation to `register_voice_websocket()` before accepting the connection:

```python
from twilio.request_validator import RequestValidator

TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
VOICE_STREAM_URL = os.getenv("VOICE_STREAM_URL", "")

@app.websocket("/voice")
async def websocket_endpoint(websocket: WebSocket) -> None:
    validator = RequestValidator(TWILIO_AUTH_TOKEN)
    url = f"{VOICE_STREAM_URL}/voice"
    signature = websocket.headers.get("X-Twilio-Signature", "")
    if not validator.validate(url, {}, signature):
        await websocket.close(code=1008)  # Policy Violation
        logger.warning("Rejected WebSocket: invalid Twilio signature")
        return
    await websocket.accept()
```

### Failure modes

- **Missing header**: Twilio always sends the signature; absence = not Twilio → reject
- **Wrong URL**: URL must match exactly what Twilio was given (including protocol, port)
- **Local dev / mock**: `MockBridge` does not use the WebSocket endpoint → unaffected
- **Tests**: Existing tests mock the FastAPI app layer → add `X-Twilio-Signature` to test headers or bypass via env flag `TWILIO_SKIP_SIGNATURE_VALIDATION=1` (test-only, documented)

## Acceptance Criteria

- [ ] `register_voice_websocket()` validates `X-Twilio-Signature` before `accept()`
- [ ] Connections with missing or invalid signature receive close code 1008
- [ ] Valid Twilio connections continue to work (live call test passes)
- [ ] Unit test: invalid signature → rejected (no `accept()` called)
- [ ] Unit test: valid signature → accepted
- [ ] `TWILIO_SKIP_SIGNATURE_VALIDATION=1` env var bypasses for local dev/test only
- [ ] Bypass documented in README and `.env.example`

## Out of Scope

- HTTP TwiML endpoint signature validation (separate concern, not yet implemented)
- Rate limiting (separate FR)
- mTLS between Twilio and server

## References

- [Twilio: Validating Requests](https://www.twilio.com/docs/usage/security)
- [Security audit 2026-05-10]: VULN-001
