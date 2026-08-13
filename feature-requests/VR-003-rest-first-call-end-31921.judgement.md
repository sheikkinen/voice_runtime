# Judgement: VR-003 REST-first call end — stop emitting Twilio 31921 on every normal call

**Judged:** 2026-08-13 via `scripts/judge.sh` (YAMLGraph adapter, model gpt-5.5); draft folded by human review same day. R-1–R-5 folded into the FR 2026-08-13.

**Verdict:** APPROVED WITH REVISIONS — the REST-first teardown direction is sound and minimal, but authority activates only after the FR folds in the evidence and testability revisions below.

**Reviewed against:** `projects/voice_runtime/feature-requests/VR-003-rest-first-call-end-31921.md`; `projects/voice_runtime/voice_runtime/transports/twilio_ws.py`; `projects/voice_runtime/voice_runtime/transports/twilio_call.py`; `projects/voice_runtime/voice_runtime/transports/_twilio_client.py`; `projects/voice_runtime/voice_runtime/session.py`; `projects/voice_runtime/tests/test_twilio_ws.py`; `projects/voice_runtime/tests/test_twilio_call.py`; `projects/voice_runtime/tests/test_vr002_twilio_http_timeout.py`; `projects/voice_runtime/CHANGELOG.md`; `projects/voice_runtime/feature-requests/VR-002-twilio-http-timeout.md`; `projects/voice_runtime/feature-requests/VR-002-twilio-http-timeout.judgement.md`; `projects/voice_runtime/feature-requests/NC-154-transport-intent-abstraction.md`; `.github/skills/judge-fr/doctrine.md`; `.github/skills/judge-fr/judgement.template.md`; `.github/copilot-instructions.md`. Cited external/csap evidence files named by the FR (`feature-requests/evidence/VBOT-87-twilio-snapshot.md`, `troubleshooting/twilio_recon.py`, `technical-release-plan.md`, Twilio 31921 docs) were not present in this repository and were not consumed.

## What is sound

The implementation target is real in this codebase: `watch_disconnect()` currently waits for `session._disconnect_requested` and then performs a server-side `websocket.close(1000)` (`twilio_ws.py:130-137`), while the `start` event stores `session.call_sid` before starting `watch_disconnect()` (`twilio_ws.py:168-182`). The REST hangup helper already exists and completes calls through `calls(call_sid).update(status="completed")` (`twilio_call.py:114-130`), and VR-002 already routes that helper through the bounded `_twilio_client` construction boundary (`_twilio_client.py:16-26`; `CHANGELOG.md:3-8`).

The change is architecturally aligned. NC-154 established that consumers express `request_disconnect()` and the transport translates that intent into protocol-specific termination (`NC-154:286-300`); this FR keeps the intent API stable and only changes the Twilio transport's "how". That matches repo doctrine to normalize at the external boundary (`.github/copilot-instructions.md:49-52`), honor existing patterns (`.github/copilot-instructions.md:214`), and write a failing witness before production changes (`.github/copilot-instructions.md:220`).

Strategic classification: **contrib/example transport-boundary defect fix**, not a YAMLGraph framework primitive. It has a named downstream consumer and a specific Twilio transport seam, but the authorized surface is limited to `projects/voice_runtime` Twilio teardown behavior.

## Required revisions

### R-1: Attach committed evidence for the 31921 defect claim

Add a committed evidence artifact, or inline an auditable excerpt in the FR, proving the two claims that make the bug real: 20/20 bot-ended calls produced 31921 and caller-initiated hangups did not. The current FR cites csap `feature-requests/evidence/VBOT-87-twilio-snapshot.md` and Twilio 31921 docs (`VR-003:12-30`, `VR-003:86-99`), but those artifacts are not present in the input closure. If the external evidence cannot be committed here, the FR must state the exact repository/path/commit or issue URL where a human can audit it and must not present unavailable evidence as already reviewed.

### R-2: Make AC-01 use the existing Twilio SDK boundary, not a fake REST endpoint

Revise AC-01 from "with a fake REST endpoint" (`VR-003:72`) to a deterministic no-network witness that patches `voice_runtime.transports.twilio_ws.hangup_call` or the underlying `twilio.rest.Client` call chain. The implementation surface has no HTTP endpoint abstraction; `hangup_call()` is a blocking SDK wrapper (`twilio_call.py:114-130`). The RED test must assert ordering: REST hangup is attempted before any server-side `websocket.close(1000)`.

### R-3: Define the inbound-close wait surface mechanically

Specify how `watch_disconnect()` observes "Twilio closed first" before fallback. The current FR says "Await the inbound WS close" (`VR-003:46`) but does not name the state signal or assertion. Fold in a concrete rule: after REST hangup succeeds, wait up to the bounded fallback interval for the receive loop to set `session.is_disconnected` via `stop`, `WebSocketDisconnect`, or equivalent (`twilio_ws.py:215-233`; `session.py:254-267`); only then may the server call `websocket.close(1000)`.

### R-4: Define terminal Twilio REST errors exactly

Replace "404/terminal-state responses are success" with the exact exception predicate to be accepted as idempotent success. Current code does not import or catch `TwilioRestException` at all (`twilio_call.py:114-130`), and no existing test covers terminal-state handling (`tests/test_twilio_call.py:161-190`). The FR must state whether only HTTP 404 is success, which Twilio error codes/messages/statuses count as terminal-state success, and that request timeouts or unknown 4xx/5xx exceptions still propagate to the transport fallback path rather than becoming success.

### R-5: Separate voice_runtime enforcement from downstream release verification

Revise AC-07 and the Release section so the enforcer cannot modify csap/ninchat_voice pins under this FR. A post-release csap TEST window with zero bot-ended 31921 events is valuable operational verification (`VR-003:86-93`), but it depends on consumer deployment outside this repository. Keep it as a post-release observation to record back into the FR, not a blocking voice_runtime implementation criterion.

## Scope is frozen

| Deliverable | Surface |
|---|---|
| D-1 | `projects/voice_runtime/voice_runtime/transports/twilio_ws.py` |
| D-2 | `projects/voice_runtime/voice_runtime/transports/twilio_call.py` |
| D-3 | `projects/voice_runtime/tests/test_vr003_rest_first_call_end_31921.py` or equivalent focused test file |
| D-4 | Minimal updates to existing Twilio transport/call tests only to preserve current behavior |
| D-5 | `projects/voice_runtime/feature-requests/VR-003-rest-first-call-end-31921.md` revision/status updates |
| D-6 | `projects/voice_runtime/CHANGELOG.md` or the voice_runtime changelog/release metadata required by that repo's process |

Not authorized: consumer repository edits, csap/ninchat_voice pin bumps, live Twilio tests as the RED witness, new dependencies, new public APIs, retries/backoff, broader transport refactors, mock transport behavior changes, incaller/outcaller flow rewrites, YAMLGraph graph/prompt changes, or changes to judge/review/enforcement doctrine.

## Revised acceptance criteria

- [ ] AC-01: With credentials and `session.call_sid` present, a disconnect request attempts `hangup_call(call_sid)` before any server-side `websocket.close(1000)`; verified by a deterministic no-network RED test using mocks.
- [ ] AC-02: After a successful REST hangup, the transport waits up to the configured fallback interval for the receive loop to observe Twilio-side close/disconnect; if that happens, no server-side close is issued.
- [ ] AC-03: REST failure, request timeout, or inbound-close timeout triggers fallback `websocket.close(1000)` within the bounded interval; the call is never left hanging.
- [ ] AC-04: A simultaneous caller hangup represented by the FR's exact terminal Twilio exception predicate is treated as idempotent success; unknown REST exceptions are not swallowed.
- [ ] AC-05: Missing credentials or missing `session.call_sid` keeps the current legacy path: no REST helper/client is called and the transport performs the existing server-side close.
- [ ] AC-06: The REST hangup runs off the media event loop via `asyncio.to_thread` or an equivalent executor; a witness proves a concurrent event-loop task continues while the mocked hangup blocks.
- [ ] AC-07: Existing caller-initiated hangup, mock transport, and flows that never call `request_disconnect()` keep their behavior; existing focused transport/call tests pass without weakened assertions.
- [ ] AC-08: A voice_runtime changelog/release note records the behavior change.
- [ ] AC-09: Post-release only: after a consumer pins the release, record the csap TEST observation for bot-ended 31921 events back into the FR; this does not authorize consumer repo changes under VR-003.

## Conditions for enforcement

| # | Condition | Severity |
|---|---|---|
| C-1 | Fold R-1 through R-5 into the FR before implementation authority is exercised. | GATE |
| C-2 | Add RED tests before production code; at minimum they must fail on the current `watch_disconnect()` server-close-first path (`twilio_ws.py:130-137`). | GATE |
| C-3 | Preserve the `request_disconnect()` public intent API; only the Twilio transport's termination mechanism may change. | GATE |
| C-4 | Do not block the event loop with the synchronous Twilio SDK call; REST hangup must execute off-loop. | GATE |
| C-5 | Do not treat missing credentials as success-with-REST; missing credentials/call SID must remain the current no-REST fallback path. | GATE |
| C-6 | Do not modify consumer repositories or pins under this FR; downstream deployment verification is recorded after release. | GATE |

Authority granted: after the required FR revisions are folded in, implement REST-first bot-initiated Twilio call teardown in voice_runtime, with bounded server-side WebSocket close fallback and deterministic no-network tests proving ordering, idempotence, and event-loop safety.
