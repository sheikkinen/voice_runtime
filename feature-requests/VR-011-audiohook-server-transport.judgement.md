# Judgement: VR-011 AudioHook server transport

**Verdict:** APPROVED WITH REVISIONS — the transport direction is sound, but implementation authority activates only after R-1 through R-5 are folded into the FR.

**Reviewed against:** `.github/skills/judge-fr/doctrine.md`; `.github/skills/judge-fr/judgement.template.md`; `.github/copilot-instructions.md`; `CLAUDE.md`; `projects/voice_runtime/feature-requests/VR-011-audiohook-server-transport.md`; `projects/voice_runtime/feature-requests/NC-154-transport-intent-abstraction.md`; `projects/voice_runtime/feature-requests/NC-236-unique-mark-names-prevent-farewell-cutoff.judgement.md`; `projects/voice_runtime/feature-requests/NC-260-stt-silent-error-gap-closure.judgement.md`; `projects/voice_runtime/feature-requests/VR-004-region-aware-hangup.md`; `projects/voice_runtime/feature-requests/VR-007-media-teardown-region-hangup.md`; `docs/vr-008-audiohook-fr:feature-requests/VR-008-audiohook-client-transport.md`; `projects/voice_runtime/voice_runtime/transports/twilio_ws.py`; `projects/voice_runtime/voice_runtime/session.py`; `projects/voice_runtime/tests/test_vr003_rest_first_call_end_31921.py`; `projects/voice_runtime/reference/release-checklist.md`; `projects/voice_runtime/pyproject.toml`.

## Human promotion record — 2026-11-03

Round 1 rendered by the YAMLGraph judge adapter (`scripts/judge.sh`, backend
copilot, model `gpt-5.6-sol`) against FR commit `cb0d863`. The owner reviewed
the verdict and approved condition C-3: a sandbox-only endpoint protected by
the API-key and organization checks alone, with a dedicated non-production
key, no real caller data, and exposure only while the spike runs. R-1 to R-5
and the revised AC-01 to AC-20 were folded into the FR (C-1), together with
the frozen scope D-1 to D-6 and the condition table. C-2 and C-4 remain open.

## What is sound

- The problem is real and the boundary is correct: Genesys is the WebSocket client, the new code is a server transport, and consumers remain on the existing `VoiceSession` intent contract (`VR-011:21-42`; `session.py:181-229,327-362`).
- The proposal disposes alternatives rather than merely listing them, explicitly distinguishes the opposite-role VR-008 client, and correctly answers "Is this a graph?" (`VR-011:215-230`; VR-008 branch artifact).
- Protocol-specific sequencing, binary framing, pacing, probe handling, authentication failure style, and unsupported-media behavior are kept inside one transport. No new runtime dependency or `VoiceSession` API is proposed (`VR-011:44-118,120-212`).
- The shared STT extraction is a narrow duplication removal: the current Twilio lifecycle is one contiguous block and can be moved without changing its contract (`twilio_ws.py:238-260`; `VR-011:145,207-212`).
- The acceptance plan covers secret hygiene, protocol envelopes, sequence faults, audio re-chunking, pacing, marks, clear, both disconnect directions, Twilio isolation, packaging, and implementation records (`VR-011:233-290`).
- **Strategic classification:** contrib/example-stage transport. One immediate consumer is named, while its wiring is separately scoped and real Genesys behavior remains unobserved (`VR-011:9,31,292-315`). It may graduate to a proven framework primitive after the field gate.
- The concern is singular: one AudioHook server transport plus its directly required helper extraction, tests, documentation, and release metadata. A split is not required.

## Required revisions

### R-1: Make the single-writer lifecycle internally consistent

Replace the contradictory startup order with one explicit rule: create the writer immediately after `accept`, before any protocol frame is sent. Every `send_text` and `send_bytes` operation -- including `opened`, authentication and media-selection `disconnect`, `pong`, `updated`, `closed`, `event`, normal `disconnect`, and binary audio -- must be queued to that writer. The writer assigns `seq` and `clientseq` at write time and provides a completion acknowledgement for terminal frames that must be flushed before socket close.

For an authenticated call, queue and await `opened` before binding the session, invoking the STT factory, or starting STT. For a probe, queue and await `opened` without binding the session. For an authentication failure, use the header session id and the last received client sequence, if any, to send the one terminal envelope through the same writer.

The current text says to start the writer only after sending `opened`, while also requiring one writer to own every send (`VR-011:140-145,160-163`). AC-10 tests only the streaming path and therefore does not close the contradiction (`VR-011:265-266`).

### R-2: Define binding and teardown as an exact state machine

Add these mechanically enforceable lifecycle rules:

1. A session becomes **bound** only after API-key/header/open organization checks pass, compatible media is selected, and `opened` has been flushed.
2. Authentication failures, probes, and unsupported-media failures before binding never mutate `call_sid`, `caller_number`, `stream_sid`, STT, or disconnected state.
3. After binding, one handler-level `finally` owns teardown. It calls `signal_disconnected()` exactly once, stops STT once, then cancels and awaits the writer and other child tasks.
4. Required terminal frames are flushed before teardown cancellation. A send failure skips impossible flushes but still performs the bound-session teardown.
5. `closed` is flushed before socket close on peer `close`; bot disconnect waits for peer `close` up to the configured bound, flushes `closed` when received, then closes.

This reconciles "session is not touched" on authentication failure with "every exit path" teardown (`VR-011:136,181-191`) and replaces the current Twilio precedent's distributed disconnect calls with an explicit exact-once contract (`twilio_ws.py:127,145,276-315`).

### R-3: Specify mark and clear ordering at the writer boundary

Fold the following definitions into Marks, Barge-in, and their acceptance criteria:

- When the writer consumes a mark, it drains the audio currently available into its pacer buffer and snapshots the scheduled-byte target. Audio arriving after that snapshot belongs after the mark.
- If the target exceeds the sent byte clock, resolve after the packet containing the target is sent plus `mark_latency_s`.
- If the target equals the sent byte clock, including a mark with no preceding audio, use the mark-consumption loop time as the latency anchor. Do not refer to a nonexistent "packet containing the target."
- When the writer consumes a clear request, it discards the pacer buffer, drains the then-queued `session.outbound` bytes, snapshots all names present in `_pending_marks`, sends one `barge_in` event, then resolves that snapshot. Audio already handed to the socket is not retractable.
- The measurable boundary is: after the `barge_in` event, no binary frame may contain bytes that were buffered before the clear was consumed. At most one already in-flight binary send may precede the event.

The existing session exposes marks and clear on separate queues (`session.py:81-92,181-229,350-362`), while the FR currently leaves the cross-queue boundary undefined and demands the unmeasurable absolute "no further audio from before the clear" (`VR-011:165-181,269-275`).

### R-4: Repair the RED and pacing acceptance contradictions

Replace AC-01 with a collection-safe RED rule. The test-only commit must collect successfully. One existence test may use `importlib.util.find_spec` and fail by assertion while `audiohook_server.py` is absent; the remaining behavioral tests must skip only on that missing-module condition. Once the module exists, no test may fail from import, fixture, or setup errors. This satisfies the judge doctrine's testability rule; the present missing-module plan cannot also guarantee non-import failures (`VR-011:235`; judge doctrine `:58-61`).

Replace AC-09's single payload with two fake-clock cases:

1. Exactly 80,000 bytes produce 50 unpadded 1,600-byte packets.
2. 80,160 bytes produce 51 packets, with the last 1,440 bytes equal to `0xFF`.

Assert the production packet interval constant is `0.2`, inject or patch the clock/sleep for scheduling tests, and assert packet timestamps are separated by at least the configured interval. Ten seconds of PCMU is exactly divisible by 1,600, so the current requirement for a padded final packet is contradictory (`VR-011:262-264`).

Also update AC-11 and AC-13 to use the R-3 empty-mark and post-`barge_in` boundaries.

### R-5: Make real protocol evidence and the security decision release gates

Change the field-evidence statement so it does not claim that E-1/E-2 are irrelevant to release. Fake-socket tests authorize implementation, not publication as a proven Genesys transport.

- **E-1, pre-merge gate:** run the pinned purecloudlabs reference client against a temporary FastAPI app registering this transport. Record open/opened, bidirectional audio, close/closed, sequence results, and absence of protocol `error`. This must not depend on the separately scoped outcaller wiring.
- **E-2, pre-release gate:** before any release tag or upload containing the public transport, run the stated Genesys sandbox/ngrok spike and record the evidence already enumerated in the FR.
- Until E-2 passes, README and CHANGELOG must label the transport **experimental, sandbox-only**, with RFC 9421 verification absent.
- Record an explicit operator decision before E-2: approve exposing a sandbox endpoint using only the API-key and organization checks, with a dedicated non-production key and no production caller data. Production deployment is not authorized by this FR.

The FR's purpose is to hold a real Genesys conversation and learn the unknown playback/rate-limit behavior (`VR-011:31-39`), but it currently makes that evidence non-gating (`VR-011:292-315`) while intentionally omitting signature verification (`VR-011:137`). That product/security decision cannot be absorbed by the judge.

## Scope is frozen

| Deliverable | Surface |
|---|---|
| D-1 | `projects/voice_runtime/voice_runtime/transports/audiohook_server.py` with the registered `/audiohook` transport |
| D-2 | One private STT-start helper under `projects/voice_runtime/voice_runtime/transports/` and the minimal `twilio_ws.py` call-site replacement |
| D-3 | `projects/voice_runtime/tests/test_vr011_audiohook_server.py` |
| D-4 | `projects/voice_runtime/README.md` AudioHook registration and experimental-limit documentation |
| D-5 | `projects/voice_runtime/pyproject.toml` minor version bump and `projects/voice_runtime/CHANGELOG.md` entry |
| D-6 | VR-011 implementation record, witness map, commands, results, deviations, E-1, and later E-2 evidence |

Not authorized: RFC 9421 verification; nonce/replay storage; a shared `audiohook/` protocol package; changes to `VoiceSession`; VR-008 rebasing; output-variable content; pause/resume transmission; reconnect; DTMF behavior beyond logging; mark options B/C; concurrent calls on one session; outcaller, CSAP, or csap-black-box-tests wiring; CI/hook/doctrine changes; release tag or upload before C-4.

## Revised acceptance criteria

- [ ] AC-01: A collection-safe, test-only RED commit precedes production code. With the module absent, one `find_spec` assertion fails and dependent behavioral tests skip; with the module present, failures are behavioral assertions, never import, fixture, or setup errors. Every new test carries `@pytest.mark.req("VR-011")`.
- [ ] AC-02: Registration rejects a non-string/empty `api_key` and negative or non-finite `mark_latency_s` before route addition. Secret values appear in no exception, `repr`, or captured log.
- [ ] AC-03: Wrong API key and header/open organization mismatches accept the upgrade, wait at least 500 ms, send exactly one generic `unauthorized` envelope through the single writer, flush it, and close. No session or STT state is touched.
- [ ] AC-04: A probe `open` receives `opened` within 5 s with the offered PCMU media unchanged, then `closed` for peer `close`; it never binds the session or creates STT.
- [ ] AC-05: A call `open` receives `opened` as the first outbound frame and within 5 s. Only after it is flushed are identity, `stream_sid`, STT factory/tee, `on_stt_ready`, and `stt.start` activated. Missing compatible media sends one `error` disconnect and never binds.
- [ ] AC-06: Every outbound text frame on every tested path has version `2`, the header session id, contiguous writer-assigned `seq`, current `clientseq`, and `parameters`; none has `position`. Ping/update receive pong/updated within one packet interval.
- [ ] AC-07: A repeated or skipped Genesys `seq` emits one ERROR record, sends one `error` disconnect when possible, and tears down a bound session exactly once.
- [ ] AC-08: Binary inputs of 1, 159, 160, 333, and 4,000 bytes are concatenated and emitted to `put_inbound`/`tap_caller` as identical consecutive 160-byte frames; the final incomplete remainder is explicitly discarded on teardown and never emitted as a short frame.
- [ ] AC-09: With a fake clock, 80,000 bytes produce 50 unpadded 1,600-byte packets; 80,160 bytes produce 51 packets with exactly 1,440 trailing `0xFF` bytes. The production interval constant is `0.2`, and send timestamps are never closer than the configured interval.
- [ ] AC-10: All text and binary sends, including authentication, probe, unsupported-media, control, clear, and ending paths, record one writer-task identity. Text queued during audio is flushed within one packet interval.
- [ ] AC-11: Marks use the R-3 scheduled-byte snapshot. Two marks resolve in queue order. A no-audio mark resolves no earlier than `mark_latency_s` after writer consumption. Playback events resolve no marks.
- [ ] AC-12: Each playback-started/completed event produces one INFO record containing its position, byte-clock seconds, latest resolved mark, and elapsed time since that mark, without secret material.
- [ ] AC-13: On clear, at most one already in-flight binary send precedes the single `barge_in` event; no later binary frame contains pre-clear buffered bytes; marks pending at clear consumption resolve.
- [ ] AC-14: Bot disconnect flushes `disconnect(completed, outputVariables={})`, waits at most the production 10-second constant for peer close, flushes `closed` if received, and closes in both response and timeout cases using a fake clock in tests.
- [ ] AC-15: Peer close flushes `closed` within 10 s. WebSocket disconnect, receive failure, send failure, sequence failure, and bot/caller endings stop STT once, cancel/await child tasks, and call `signal_disconnected()` exactly once only when the session was bound.
- [ ] AC-16: The shared STT helper preserves the existing Twilio lifecycle and call order. Existing Twilio, VR-003, and VR-007 tests pass unmodified. Importing `audiohook_server` does not import `twilio` or `twilio_ws`.
- [ ] AC-17: `pytest tests/ -q --no-cov`, `ruff check voice_runtime/`, and wheel build pass; no runtime dependency is added; the minor version and CHANGELOG entry are present; the wheel excludes tests, feature requests, and `.env*`.
- [ ] AC-18: README documents the registration call, byte-clock marks, empty output variables, single-call session scope, absent signature verification, and the experimental sandbox-only status until E-2.
- [ ] AC-19: E-1 is recorded before merge. E-2 and the explicit sandbox-risk decision are recorded before any release tag or upload containing this feature.
- [ ] AC-20: The FR records RED/GREEN commits, an AC-to-test witness map, focused/full-suite commands and results, artifact inspection, field evidence, and every deviation.

## Conditions for enforcement

| # | Condition | Severity |
|---|---|---|
| C-1 | Fold R-1 through R-5 and the revised AC list into VR-011 before production edits. | GATE |
| C-2 | Preserve RED-before-GREEN history; the RED commit must satisfy revised AC-01. | GATE |
| C-3 | A human operator must approve the documented sandbox-only, dedicated-key exposure before the real Genesys spike; no production traffic or caller data is authorized. | GATE |
| C-4 | Do not tag or publish a release containing the transport until E-2 is recorded and passes. | GATE |
| C-5 | Do not add any surface listed as not authorized; adjacent work requires a separate judged FR. | GATE |

Authority granted: after C-1, implement D-1 through D-6 within the frozen scope; public release remains blocked by C-3 and C-4.
