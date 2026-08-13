# Feature Request: VR-003 REST-first call end — stop emitting Twilio 31921 on every normal call

**Priority:** HIGH (blocks clean alerting for the Tervola pilot watch, VBOT-88)
**Type:** Bug fix / behavior change
**Status:** Enforced 2026-08-13 (RED c419744, GREEN f27567f) — AC-01–AC-08 met; AC-09 pending post-release
**Effort:** 0.5 day
**Requested:** 2026-08-13
**Downstream:** ninchat_voice / csap (VBOT-88 correlator alerting, Tervola release plan)

## Defect

Every bot-terminated call generates a Twilio debugger event
**error 31921 (Stream — WebSocket — Close Error)**. Field evidence (R-1,
auditable): csap repo `terveystalo/customer-service-agent-platform`,
commit `11480ed` (VBOT-87 PR #132),
`feature-requests/evidence/VBOT-87-twilio-snapshot.md` §“Extension run
(2026-08-12, full incident recon)”:

> **Debugger alerts**: 20 × `[error] code=31921` (Media Streams WebSocket
> closed unexpectedly), 2026-08-11 07:17–09:07Z, one per test call —
> **our media-WS teardown registers as an error on Twilio's side on
> every call**.

(`Recent calls: completed=20` in the same window — i.e. 20/20 bot-ended
calls produced exactly one 31921 each.)

Mechanism (verified against Twilio docs for 31921): in bidirectional
`<Connect><Stream>`, **any server-side WS close** — including our clean
`close(1000)` — is logged by Twilio as an error. Our disconnect path is
WS-close-first:

- `transports/twilio_ws.py:134-137`: `await session._disconnect_requested.wait()`
  → `await websocket.close(1000)`.

Caller-initiated hangups (Twilio closes first) do NOT produce 31921 —
so production traffic yields a mixed signal in which a **genuine
mid-call stream crash (same code 31921) is indistinguishable from a
normal bot-ended call**. The downstream correlator/alerting
(csap VBOT-88, pilot supervisors) needs 31921 to be a true signal.

## Fix contract: REST hangup first, WS close as fallback

Verified building blocks (2026-08-13 pre-judgement check): `session.call_sid`
is populated from the WS `start` event (`transports/twilio_ws.py:176`), and
`hangup_call(call_sid)` already exists (`transports/twilio_call.py`, 0.1.9,
NC-362) via `_twilio_client.py` with VR-002 bounded timeout — the fix reuses
it rather than adding a new REST path.

When `_disconnect_requested` fires and a call SID + REST credentials are
available:

1. Issue Twilio REST hangup (`POST /2010-04-01/Accounts/{sid}/Calls/{call_sid}.json`,
   `Status=completed`) — Twilio then closes the media WS from ITS side
   (no 31921).
2. Await the inbound WS close (bounded wait, e.g. 5 s). Mechanical
   definition (R-3): after REST hangup succeeds, wait up to the bounded
   fallback interval for the receive loop to observe the Twilio-side
   close — the `stop` event or `WebSocketDisconnect` setting the
   session's disconnected state (`twilio_ws.py` receive loop →
   `session.is_disconnected`). Only if that signal does not arrive may
   the server issue its own close.
3. Fallback: if REST fails or the close doesn't arrive in time, close the
   WS server-side as today (`close(1000)`) — never leave a call hanging.

Constraints:

1. No new dependencies; the REST call goes through the existing
   voice_runtime transport/HTTP layer (NC-154: no telephony SDK; VR-002
   timeout discipline applies).
2. No behavior change for caller-initiated hangups, mock transport, or
   the incaller/outcaller flows that never request disconnect.
3. Credentials/call-SID absent (local dev, tests) → current WS-close
   behavior, unchanged.
4. The disconnect must remain idempotent: REST hangup racing a
   simultaneous caller hangup must not raise. Exact terminal predicate
   (R-4): `TwilioRestException` with `status == 404` (call gone), or
   `status == 400` with Twilio error `code == 21220` (call not
   in-progress / already terminal) → idempotent success. Any other
   exception — timeouts, unknown 4xx/5xx — propagates to the transport
   fallback path (WS close), never swallowed as success. **Gap in
   current code:** `hangup_call` only guards missing credentials — a
   terminal-state `TwilioRestException` propagates uncaught; the fix
   must extend `hangup_call` (or wrap it) with exactly this predicate.
5. `hangup_call` is a blocking SDK call but `watch_disconnect()` runs on
   the media event loop — the REST hangup MUST be offloaded
   (`asyncio.to_thread`) so teardown never blocks the loop (same
   blocking-in-loop defect class as FR-708/NC-361).

## Acceptance Criteria

Revised per judgement (2026-08-13); supersedes the draft ACs.

- [ ] AC-01: With credentials and `session.call_sid` present, a
      disconnect request attempts `hangup_call(call_sid)` BEFORE any
      server-side `websocket.close(1000)`; verified by a deterministic
      no-network RED test that patches
      `voice_runtime.transports.twilio_ws.hangup_call` (R-2 — no fake
      REST endpoint; there is no HTTP abstraction to fake).
- [ ] AC-02: After a successful REST hangup, the transport waits up to
      the bounded fallback interval for the receive loop to observe the
      Twilio-side close; if observed, no server-side close is issued.
- [ ] AC-03: REST failure, request timeout, or inbound-close timeout
      triggers fallback `websocket.close(1000)` within the bounded
      interval; the call is never left hanging.
- [ ] AC-04: The exact terminal predicate (constraint 4) is treated as
      idempotent success; unknown REST exceptions are NOT swallowed.
- [ ] AC-05: Missing credentials or missing `session.call_sid` → legacy
      path: no REST helper/client called (witness asserts no attempt),
      existing server-side close unchanged.
- [ ] AC-06: The REST hangup runs off the media event loop
      (`asyncio.to_thread` or equivalent); witness proves a concurrent
      loop task keeps ticking while the mocked hangup blocks.
- [ ] AC-07: Caller-initiated hangup, mock transport, and flows that
      never call `request_disconnect()` keep their behavior; existing
      transport/call tests pass without weakened assertions.
- [ ] AC-08: CHANGELOG entry records the behavior change.
- [ ] AC-09 (post-release observation only, R-5): after a consumer pins
      the release, record the csap TEST zero-31921 observation for
      bot-ended calls back into this FR (via
      `troubleshooting/twilio_recon.py` alerts section). Does NOT
      authorize consumer repo changes under VR-003.

## Release

Patch release + version bump. Consumer pin bumps (csap uv.lock sync,
same flow as 0.1.11) happen OUTSIDE this FR's authority (R-5/C-6);
AC-09 is recorded here after csap picks the release up independently.

## Related

- `VR-003-rest-first-call-end-31921.judgement.md` — APPROVED WITH
  REVISIONS, scope freeze, conditions C-1–C-6
- Twilio error 31921 docs (server-side close of bidirectional stream)
- csap VBOT-87 evidence (cited exactly in Defect § above), VBOT-88
  correlator (consumer of the cleaned signal), technical-release-plan.md
  (pilot watch)
- NC-362 (csap): REST hangup precedent at the reap boundary
- VR-002: Twilio HTTP timeout discipline

## Implementation record (2026-08-13)

- RED `c419744`: 10 witnesses in
  `tests/test_vr003_rest_first_call_end_31921.py` — 6 failed on the
  server-close-first path, 4 no-regression witnesses (legacy path,
  error propagation) passed.
- GREEN: `rest_hangup_first()` in `twilio_ws.py` (REST via
  `asyncio.to_thread(hangup_call, call_sid)`, then 0.05 s-polled bounded
  wait on `session.is_disconnected`, `REST_CLOSE_WAIT_S = 5.0`);
  terminal predicate (404 / 400+21220 → success) inside `hangup_call`.
- Deviation: `TwilioRestException` imported lazily inside `hangup_call` —
  the VR-002 witness `test_no_module_level_twilio_imports` forbids
  module-level twilio imports in transports (caught by the full-suite
  run, one-line fix).
- Full suite: 271 passed, 48 skipped, 0 failed.
- AC-09 open: record csap TEST zero-31921 observation after a consumer
  pins the release.
