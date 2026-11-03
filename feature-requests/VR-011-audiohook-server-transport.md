# VR-011 — AudioHook server transport (Genesys Audio Connector, minimal PoC)

**Priority:** MEDIUM
**Type:** Feature (new transport; Twilio transports unchanged)
**Status:** Approved with revisions. R-1 to R-5 are folded in (C-1); implementation is authorized within the frozen scope D-1 to D-6.
**Requested:** 2026-11-03
**Effort:** 1.5–2 days (library and fake-socket tests). The field spike is extra and depends on getting access to a Genesys org.
**Target release:** the next minor release after 0.3.0. A new public function counts as a feature (`reference/release-checklist.md`).
**First consumer:** outcaller's inbound app (`server_incaller.py`). It gains one registration line and a start script without the Twilio webhook. That is a separate change and does not gate this FR. Later consumers, CSAP (`WS /genesys` on the supervisor) and csap-black-box-tests, get their own FRs.
**Prior art:** `register_voice_websocket` (`voice_runtime/transports/twilio_ws.py:78`, the shape to mirror); NC-154 (transport intent: `request_disconnect`, `request_clear_buffer`); NC-236 (unique mark names); NC-260 (disconnect signalling); VR-004 (secrets kept out of `repr` and logs, and no reading of consumer environment variables); VR-007 (FR format); VR-008 (draft AudioHook **client**, unjudged, on branch `docs/vr-008-audiohook-fr`).
**Research:** [Dispositioned alternatives](#research-and-prior-art). The protocol facts it relies on are restated in [Protocol facts](#protocol-facts-this-fr-relies-on), with sources pinned to commits.
**Judgement:** [APPROVED WITH REVISIONS](VR-011-audiohook-server-transport.judgement.md), round 1, 2026-11-03. Conditions C-1 to C-5; the C-3 operator decision is recorded under [Field evidence](#acceptance-criteria).

## Glossary

- **AudioHook:** the WebSocket protocol, version 2, that Genesys Cloud uses to stream call audio to an external server.
- **Audio Connector:** the Genesys Architect action that uses AudioHook **in both directions**. The caller's audio comes to us, and our bot's audio goes back to the caller. Our use case.
- **Client / server:** Genesys is the **client**: it opens the WebSocket. We are the **server**. VR-008 is the opposite role (we act as Genesys).
- **Probe session:** a session Genesys opens when an integration is set up or tested. Its `open` carries the nil UUID `00000000-0000-0000-0000-000000000000` as `conversationId` or `participant.id` (corrected by E-1; JSON `null` is also treated as a probe), and no caller is on the line.
- **Mark:** voice_runtime's playback sync point. `VoiceSession.send_mark_and_wait(name)` blocks until the transport calls `signal_mark_received(name)`. On Twilio, Twilio echoes the mark when playback reaches it. AudioHook has **no wire-level mark**, so this transport emulates one.
- **Byte clock:** the number of outbound audio bytes this transport has handed to the socket. PCMU at 8 kHz is 8000 bytes per second, so the bytes sent divided by 8000 approximates the seconds of audio handed over.
- **PCMU:** G.711 μ-law, 8 kHz, mono. 1 byte per sample. μ-law silence is `0xFF`.
- **Spike:** an exploratory run against a real Genesys org, whose result is recorded as evidence. It is not a unit test.

## Problem and ideal result

voice_runtime connects a `VoiceSession` to exactly one telephony network: Twilio Media Streams (`twilio_ws.py`). Calls that arrive through Genesys Cloud cannot reach any voice_runtime consumer. Genesys's supported route for an external voicebot is Audio Connector, which speaks AudioHook. The protocol differs from Twilio in authentication, framing, sequencing, pacing and playback signalling (see Protocol facts).

The consumers above the transport (`TelcoSession`, the CSAP FSM, the outcaller graph) are already independent of the network: they use only the `VoiceSession` contract (`put_inbound`, `outbound`, `send_mark_and_wait`, `request_disconnect`, `request_clear_buffer`, `signal_*`). A second transport behind the same contract therefore lets a bot answer a Genesys call with no change above the transport.

We have no Genesys org access yet, and the main protocol unknowns (§ Open questions) can only be answered against a real org. The first step is therefore deliberately minimal: one server transport that is good enough for outcaller to hold a real conversation over Genesys and to **record evidence** about the unknowns. Production concerns (full signature verification, a shared protocol module, CSAP supervisor routing, returning data to Architect) follow in later FRs, informed by that evidence.

**Ideal result:** `register_audiohook_websocket(app, session, api_key=...)` registers a WebSocket route that:
- accepts a Genesys Audio Connector session;
- feeds the caller's audio to STT;
- plays the bot's TTS back within Genesys's rate limit;
- resolves marks so turn-taking works as it does on Twilio;
- ends cleanly from either side.

It also logs the playback evidence the spike needs. Base install, Twilio behaviour and every existing public signature are unchanged.

## Protocol facts this FR relies on

Taken from the Genesys AudioHook protocol reference and security pages, and from the TypeScript message types of Genesys's two reference implementations: `purecloudlabs/audiohook-reference-implementation` @ `2871b01` and `GenesysCloudBlueprints/audioconnector-server-reference-implementation` @ `a167bb2`, both read 2026-11-03. Items marked *(unverified)* come from third-party code only.

**F-1 Upgrade.**
- Genesys sends `GET` with a WebSocket upgrade, over WSS on port **443 only**, and needs a **public-CA certificate** (an ngrok tunnel qualifies).
- Headers:
  - `X-API-KEY`: the key configured on the integration;
  - `Audiohook-Organization-Id`, `Audiohook-Session-Id`, `Audiohook-Correlation-Id`;
  - `Audiohook-Retry-Attempt`: on retries only;
  - `Signature-Input` and `Signature` (RFC 9421, HMAC-SHA256): only when a client secret is configured on the integration.

**F-2 Rejection style.**
- Genesys's reference server **accepts** the upgrade. On an authentication failure it sends a `disconnect` message with `reason: "unauthorized"`; it does not refuse the upgrade with HTTP 401.
- It answers a failure no sooner than **500 ms** after verification started, which reduces timing side channels.
- A bare socket close makes Genesys retry.

**F-3 Envelope.**
- Text frames are JSON: `{"version":"2","id":<session id>,"type":...,"seq":n,"parameters":{...}}`.
- Each side numbers **its own** text messages, starting at 1 and increasing by exactly 1.
- Messages from Genesys also carry `serverseq` (the last `seq` it received from us) and `position` (an ISO 8601 duration, e.g. `PT12.34S`).
- Our messages carry `clientseq` (the last Genesys `seq` we received) and **no** `position`.
- `parameters` must always be present, `{}` when empty.
- Binary frames carry raw audio with no header and no sequence number.

**F-4 Sequencing is fatal.**
- Genesys treats an out-of-order or duplicated `seq` from us as fatal (`error` code 409).
- The reference server applies the same rule to Genesys's `seq`.
- Two coroutines writing to one socket can easily break ordering, so all outbound messages go through **one writer**.

**F-5 Messages from Genesys:**

| Message | Contents and our duty |
|---|---|
| `open` | Contains `organizationId`, `conversationId` (nil UUID for a probe; corrected by E-1), `participant{id, ani, aniName, dnis}`, offered `media[]`, `language` and `inputVariables`. We must answer `opened` **within 5 s**. |
| `ping` | Answer `pong` within 5 s. |
| `close` | `reason` is `end`, `error`, `disconnect` (the answer to our `disconnect`) or `reconnect`. We must answer `closed` **within 10 s**. |
| `update` | Answer `updated`. |
| `playback_started`, `playback_completed` | Carry a `position` and **no name**. They cannot be correlated with a particular clip. |
| `paused`, `resumed`, `discarded`, `dtmf` | Informational for this FR. |
| `error` | Carries a code (400, 405, 408, 409, 413, 415, 429, 500, 503); 429 includes `retryAfter`. |

**F-6 Messages from us:**
- `opened`: chooses exactly one offered media entry, unchanged.
- `pong`, `updated`, `closed`.
- `event`: `entities:[{type:"barge_in",data:{}}]` stops Genesys playback.
- `disconnect`: `reason` is one of `completed`, `unauthorized` or `error` (no other values exist in Genesys's types); optional `info`; optional `outputVariables`, a string map returned to the Architect flow.

**F-7 Media.**
- We choose PCMU, 8000 Hz, channel `external`. This is the format our μ-law pipeline already uses on Twilio.
- Inbound binary frames vary in size. Do not assume Twilio's 160-byte (20 ms) frames.
- On connect, Genesys may send buffered audio in a burst *(unverified)*.

**F-8 Outbound limits.**
- At most **64,000 bytes** per binary message; above that Genesys answers 413.
- At most **5 binary messages per second** on average, with bursts up to 25 *(corroborated, not verified)*: Rasa cites the Genesys limits page, which could not be read without logging in. Too fast gets 429.
- Twilio-sized 20 ms frames would be 50 messages per second.
- Pipecat paces fixed **1600-byte** (200 ms) packets "to prevent 429 rate limiting from Genesys", which is exactly 5 per second in real time.

**F-9 Playback signals.**
- How Genesys groups `playback_started` and `playback_completed` (per burst? per gap? per clip?) is **undocumented**.
- Genesys's own Audio Connector server uses the pair only as an "is the bot speaking" flag.
- No surveyed implementation (Pipecat, Rasa, Azure art accelerator, Genesys blueprint) maps them to named marks. Rasa makes its marker hook a no-op.

**F-10 Ending.**
- Bot ends: we send `disconnect(completed)`; Genesys replies `close`; we answer `closed`.
- Caller ends: Genesys sends `close`; we answer `closed`.
- Whether Genesys still plays audio it has buffered after `disconnect` is unknown. Until that is known, the consumer waits for the farewell mark before requesting disconnect, as it does on Twilio today.

## Proposed solution

### 1. `voice_runtime/transports/audiohook_server.py` (new)

```python
def register_audiohook_websocket(
    app: FastAPI,
    session: VoiceSession,
    *,
    api_key: str,
    organization_id: str | None = None,
    path: str = "/audiohook",
    mark_latency_s: float = 0.2,
) -> None: ...
```

**Registration.**
- `api_key` must be a non-empty string; `mark_latency_s` must be ≥ 0. Otherwise raise `ValueError` at registration, before the route exists.
- `api_key` never appears in a log record, an exception message or a `repr`.
- The library reads **no environment variables** (VR-004 rule). The consumer supplies every value.

**Upgrade and authentication (PoC level).**
- Accept the upgrade, as F-2 describes.
- Compare `X-API-KEY` with `api_key` in constant time (`hmac.compare_digest`).
- When `organization_id` is given, `Audiohook-Organization-Id` must equal it, and so must `open.organizationId`.
- **Failure path.** Wait until at least 500 ms have passed since accept, then queue `disconnect{reason:"unauthorized"}` on the single writer and await its flush. It uses the header session id and the last Genesys `seq` received, if any, as `clientseq`. Its `info` is generic and never says which check failed. Then close the socket. The session is never bound: no `signal_ws_connected`, no `call_sid`, no STT, no `signal_disconnected`.
- `Signature` / `Signature-Input` are **not verified** in this FR. They are logged as present or absent, never by value. The PoC integration is configured without a client secret if Genesys allows it (open question Q-6). Verification belongs to the shared-module FR.

**Session lifecycle (judgement R-1, R-2).**
- **Writer first.** The writer task is created immediately after `accept`, before any protocol frame is sent (see Single writer).
- **Bound.** A session becomes *bound* only after the API-key, header and `open` organization checks pass, compatible media is selected, and `opened` has been flushed. Before that point (authentication failure, probe, unsupported media) the handler never mutates `call_sid`, `caller_number`, `stream_sid`, STT or disconnected state.
- **Probe.** On `open` whose `conversationId` or `participant.id` is the nil UUID, or whose `conversationId` is JSON `null` (corrected by E-1): queue `opened` with the chosen media and await its flush, then answer the later `close` with `closed`. Never bind the session or create STT.
- **Call.** On any other `open`:
  1. Choose the offered PCMU 8000 `external` entry unchanged. If none is offered, send `disconnect{reason:"error"}` through the writer, flush it and close without binding.
  2. Queue `opened` as the first outbound frame and await its flush.
  3. Bind: set `session.call_sid = conversationId` and `session.caller_number = participant.ani`, then call `session.signal_ws_connected(<Audiohook session id>)`. The session id doubles as `stream_sid`, which `request_clear_buffer` needs.
  4. Start the STT lifecycle exactly as `twilio_ws` does today: factory, optional tee, `on_stt_ready`, then `stt.start(session.inbound)`. That block (`twilio_ws.py`, the code after `event == "start"`) moves into one private helper in `voice_runtime/transports/` that both transports call. It is moved, not changed: existing Twilio tests pass unmodified.
- **Inbound sequence check.** Each Genesys `seq` must be the previous one plus 1. Any other value: log at ERROR, send `disconnect{reason:"error"}`, and end the session (F-4).
- **Control messages.**
  - `ping` → `pong`; `update` → `updated`.
  - `paused`, `resumed`, `discarded` → log at INFO with their parameters.
  - `dtmf` → log at INFO.
  - `error` → log at WARNING with code and `retryAfter`.
  - `playback_started` / `playback_completed` → see Marks.
  - An unknown `type` → log at WARNING and ignore.

**Inbound audio.**
- Binary frames are re-chunked into exact `FRAME_BYTES` (160-byte) frames, keeping the remainder for the next frame. An incomplete remainder left at teardown is discarded, never emitted as a short frame.
- Each full frame goes to `session.put_inbound` and `session.tap_caller`. Consumers keep seeing Twilio-sized frames (F-7).

**Single writer and pacing (F-4, F-8).**
- One writer task, created immediately after `accept`, owns every `send_text` / `send_bytes` on the socket: `opened`, authentication and media-selection `disconnect`, `pong`, `updated`, `closed`, `event`, normal `disconnect`, and binary audio. No other code path writes to the socket.
- Text messages are written in the order they are queued, with `seq` assigned at write time and `clientseq` set to the last Genesys `seq` seen.
- Terminal frames (`opened`, every `disconnect`, `closed`) carry a completion acknowledgement; the handler awaits it before binding, closing or tearing down.
- **Outbound audio:**
  - Drain `session.outbound` into a pacer buffer and send it as **1600-byte** binary messages, at most one per 200 ms on the loop clock.
  - A trailing partial packet is padded with `0xFF` to 1600 bytes when the queue has been empty for one packet interval, or when a mark is waiting on it.
  - Text messages never wait behind audio for more than one packet interval.

**Marks: byte-clock emulation, option A (judgement R-3).**
- When the writer consumes a mark from `session.get_pending_mark()`, it first drains the audio currently in `session.outbound` into the pacer buffer, then snapshots the scheduled-byte target: the total outbound byte count at that moment, including padding. Audio arriving after the snapshot belongs after the mark.
- If the target exceeds the sent byte clock, the mark resolves (`session.signal_mark_received(name)`) once the packet containing the target has been sent **and** `mark_latency_s` has elapsed since that send.
- If the target equals the sent byte clock (including a mark with no preceding audio), the latency anchor is the loop time at which the writer consumed the mark.
- Marks resolve in the order they were queued.
- **Evidence for the spike.** Every `playback_started` / `playback_completed` is logged at INFO with:
  - its `position`;
  - the byte clock in seconds;
  - the name of the most recently resolved mark;
  - the time since that mark resolved.

  These events never resolve a mark in this FR. Options B and C (playback events as barrier or confirmation) wait for that evidence.

**Barge-in (`request_clear_buffer`, NC-154; judgement R-3).** When the writer consumes a clear request, in this order:
1. Discard the pacer buffer and drain the then-queued `session.outbound` bytes.
2. Snapshot every name present in `_pending_marks`.
3. Send one `event{entities:[{type:"barge_in",data:{}}]}`.
4. Resolve the snapshot. This mirrors Twilio, which echoes pending marks after `clear`.

Audio already handed to the socket cannot be retracted. The measurable boundary: after the `barge_in` event, no binary frame contains bytes buffered before the clear was consumed, and at most one already in-flight binary send precedes the event.

**Ending.**
- **Bot ends.** On `session.request_disconnect()` (`_disconnect_requested`):
  - send `disconnect{reason:"completed", outputVariables:{}}` and await its flush;
  - wait up to 10 s (a module constant) for Genesys's `close`; if it arrives, flush `closed`, then close the socket;
  - if no `close` arrives, close the socket after the wait.
- **Caller ends.** On Genesys `close`: flush `closed{}` before closing the socket.
- **Teardown (judgement R-2).** After binding, one handler-level `finally` owns teardown. It calls `session.signal_disconnected()` exactly once (NC-260 Gap B semantics), stops STT once, then cancels and awaits the writer and other child tasks. Required terminal frames are flushed before cancellation; when a send has already failed, impossible flushes are skipped but the bound-session teardown still runs. This covers `WebSocketDisconnect`, receive and send failures, a sequence error, and bot or caller endings. An unbound handler (authentication failure, probe, unsupported media) never calls `signal_disconnected()`.

**Import path.** Consumers import `voice_runtime.transports.audiohook_server.register_audiohook_websocket`, the same way they import `twilio_ws` today. `voice_runtime/transports/__init__.py` stays empty.

### 2. Dependencies

None. The transport uses FastAPI/Starlette's `WebSocket` (`receive()` gives text or bytes) and the stdlib `hmac`. `pyproject.toml` changes only the version. The base install and `import voice_runtime` are unchanged.

### 3. Documentation

- README: a short "AudioHook (Genesys Audio Connector)" section with the registration call.
- The section labels the transport **experimental, sandbox-only** until E-2 passes (judgement R-5), and states the PoC limits:
  - no signature verification (RFC 9421 absent);
  - byte-clock marks;
  - empty `outputVariables`;
  - one call per `VoiceSession`, as on Twilio.
- CHANGELOG entry for VR-011, with the same experimental, sandbox-only label.

### Not changed

- `VoiceSession`: no new fields and no changed signatures.
- `twilio_ws.py` behaviour, `twilio_call.py`, `mock_bridge.py`, and the STT/TTS providers. The only edit to `twilio_ws.py` is moving the STT start block into the shared helper.
- VR-008, the client draft: not re-based here.

## Research and prior art

| Alternative | Evidence | Disposition |
|---|---|---|
| Depend on `pipecat-ai` and use its Genesys serializer | v1.15.0 pulls a heavy core (frames, resampler, processors). Its serializer has two protocol errors: it adds `position` to our messages, and it reverses the pause/resume direction. Its pipeline takes turn-taking decisions that would compete with the FSM, and it has no mark equivalent. | Rejected at runtime. It may serve as a **test-only** oracle in the shared-module FR. |
| Copy Pipecat's serializer (BSD-2-Clause) | Same protocol errors. It is a serializer, not a transport, and has no authentication or probe handling. | Rejected. |
| Azure art accelerator server (MIT) | It is a full app, not a library. Its client simulator sends `playback_completed` on a guessed schedule. | Not used. Its single-writer queue design is adopted (F-4). |
| Build the shared `audiohook/` module and full RFC 9421 verification first | Correct end state. But it adds three or four modules and a signature test-vector suite before anyone has seen a real Genesys session. | Deferred to the next voice_runtime FR, informed by the spike evidence. |
| Mark emulation from `playback_completed` (B) or hybrid (C) | Grouping is undocumented (F-9). Building on it now encodes a guess (`mock_escape_hatch`). | Deferred. Option A ships with evidence logging. |
| Few large messages (Genesys blueprint: whole reply in ≤ 64,000-byte splits) | Within limits, but every byte counts as "sent" at once. That makes the byte clock useless for marks, and barge-in depends on Genesys flushing its buffer. | Rejected for this FR. Paced 1600-byte packets are chosen. |
| Refuse bad credentials with HTTP 401 | The reference server accepts the upgrade and sends `disconnect(unauthorized)` instead (F-2). | Follow the reference. |

**Is this a graph?** No. This is deterministic transport code with no LLM stage.

**Precedents in this repo:** `twilio_ws.register_voice_websocket` (route shape, task set, teardown order); `tests/test_vr003_rest_first_call_end_31921.py:55-104` (`_capture_handler` / `_make_ws`: drive the route handler with a fake socket, no server); VR-004 (secret hygiene); NC-236 (mark names are opaque and unique).

## Acceptance criteria

Every new test carries `@pytest.mark.req("VR-011")` and lives in `tests/test_vr011_audiohook_server.py`. It drives the captured route handler with a fake socket that scripts Genesys messages and records every send with a loop-clock timestamp. The pacing interval is the module constant, so no test sleeps in real time for more than about 1 s.

Revised by the judgement (round 1, R-1 to R-5). The wording below is the judgement's, adopted unchanged.

- [x] **AC-01:** A collection-safe, test-only RED commit precedes production code. With the module absent, one `importlib.util.find_spec` assertion fails and the dependent behavioural tests skip only on that missing-module condition. With the module present, failures are behavioural assertions, never import, fixture or setup errors. Every new test carries `@pytest.mark.req("VR-011")`.
- [x] **AC-02:** Registration rejects a non-string or empty `api_key` and a negative or non-finite `mark_latency_s` before the route is added. Secret values appear in no exception, `repr` or captured log.
- [x] **AC-03:** A wrong API key and header or `open` organization mismatches accept the upgrade, wait at least 500 ms, send exactly one generic `unauthorized` envelope through the single writer, flush it, and close. No session or STT state is touched.
- [x] **AC-04:** A probe `open` receives `opened` within 5 s with the offered PCMU media unchanged, then `closed` for the peer `close`. It never binds the session or creates STT.
- [x] **AC-05:** A call `open` receives `opened` as the first outbound frame and within 5 s. Only after it is flushed are identity, `stream_sid`, STT factory and tee, `on_stt_ready` and `stt.start` activated. Missing compatible media sends one `error` disconnect and never binds.
- [x] **AC-06:** Every outbound text frame on every tested path has `version "2"`, the header session id, contiguous writer-assigned `seq`, the current `clientseq` and `parameters`; none has `position`. `ping` and `update` receive `pong` and `updated` within one packet interval.
- [x] **AC-07:** A repeated or skipped Genesys `seq` emits one ERROR record, sends one `error` disconnect when possible, and tears down a bound session exactly once.
- [x] **AC-08:** Binary inputs of 1, 159, 160, 333 and 4,000 bytes are concatenated and emitted to `put_inbound` and `tap_caller` as identical consecutive 160-byte frames. The final incomplete remainder is explicitly discarded on teardown and never emitted as a short frame.
- [x] **AC-09:** With a fake clock, 80,000 bytes produce 50 unpadded 1,600-byte packets; 80,160 bytes produce 51 packets with exactly 1,440 trailing `0xFF` bytes. The production interval constant is `0.2`, and send timestamps are never closer than the configured interval.
- [x] **AC-10:** All text and binary sends, including the authentication, probe, unsupported-media, control, clear and ending paths, record one writer-task identity. Text queued during audio is flushed within one packet interval.
- [x] **AC-11:** Marks use the R-3 scheduled-byte snapshot. Two marks resolve in queue order. A no-audio mark resolves no earlier than `mark_latency_s` after writer consumption. Playback events resolve no marks.
- [x] **AC-12:** Each `playback_started` / `playback_completed` event produces one INFO record containing its position, the byte-clock seconds, the latest resolved mark and the elapsed time since that mark, without secret material.
- [x] **AC-13:** On clear, at most one already in-flight binary send precedes the single `barge_in` event. No later binary frame contains pre-clear buffered bytes. Marks pending when the clear is consumed resolve.
- [x] **AC-14:** Bot disconnect flushes `disconnect(completed, outputVariables={})`, waits at most the production 10-second constant for the peer `close`, flushes `closed` if received, and closes in both the response and timeout cases. Tests use a fake clock.
- [x] **AC-15:** Peer `close` flushes `closed` within 10 s. WebSocket disconnect, receive failure, send failure, sequence failure, and bot or caller endings stop STT once, cancel and await child tasks, and call `signal_disconnected()` exactly once, and only when the session was bound.
- [x] **AC-16:** The shared STT helper preserves the existing Twilio lifecycle and call order. Existing Twilio, VR-003 and VR-007 tests pass unmodified. Importing `audiohook_server` in a fresh interpreter does not import `twilio` or `voice_runtime.transports.twilio_ws`.
- [x] **AC-17:** `pytest tests/ -q --no-cov`, `ruff check voice_runtime/` and the wheel build pass. No runtime dependency is added. The minor version and CHANGELOG entry are present. The wheel excludes `tests/`, `feature-requests/` and `.env*`.
- [x] **AC-18:** README documents the registration call, byte-clock marks, empty output variables, single-call session scope, absent signature verification, and the experimental, sandbox-only status until E-2.
- [ ] **AC-19:** E-1 is recorded before merge. E-2 and the explicit sandbox-risk decision are recorded before any release tag or upload containing this feature.
- [ ] **AC-20:** The FR records the RED and GREEN commits, an AC-to-test witness map, focused and full-suite commands and results, artifact inspection, field evidence, and every deviation.

**Field evidence (judgement R-5).** Fake-socket tests authorize implementation, not publication as a proven Genesys transport.

- **E-1, pre-merge gate:** the pinned purecloudlabs reference **client** (Node CLI, streaming a WAV) completes a session against a temporary FastAPI app that registers this transport: open, `opened`, audio both ways, close, `closed`, contiguous sequence numbers, and no protocol `error`. It does not depend on the separately scoped outcaller wiring.
- **E-2, pre-release gate:** a Genesys sandbox Architect flow with Audio Connector reaches the transport over ngrok on port 443. The record includes the conversation id, transcript excerpts, the number of `playback_*` pairs per bot turn with their timing against the byte clock, any 429/409/413 errors, behaviour after `barge_in`, and whether audio sent just before `disconnect` was heard. No release tag or upload containing this transport happens before E-2 passes (C-4).

**Operator decision (C-3), 2026-11-03.** The owner (@sheikkinen) approved exposing a sandbox endpoint protected only by the API-key and organization checks for the E-2 spike, knowing that RFC 9421 signature verification and replay protection are absent. Bounds:

1. Traffic comes from a Genesys sandbox org only. Production deployment is not authorized by this FR.
2. A dedicated non-production API key is used, held only in local `.env`, and rotated or revoked after the spike.
3. Only test calls; no real caller or patient data.
4. The endpoint (ngrok tunnel) is reachable only while the spike runs.

## Open questions (answered by the spike, not by this FR)

1. **Q-1** Playback grouping: how many `playback_started` / `playback_completed` pairs come per clip, and with what delay against the byte clock? (Decides mark options B or C.)
2. **Q-2** Rate limit: are 5 per second and burst 25 the real values for our org? (`GET /api/v2/organizations/limits/namespaces/audiohook/defaults`.)
3. **Q-3** After `barge_in`: how much audio still plays, and does a `playback_completed` follow?
4. **Q-4** After `disconnect`: is buffered audio still played?
5. **Q-5** Does Genesys send a buffered-audio burst on connect, and does it honour `discardTo` in `opened`? (Not sent in this FR.)
6. **Q-6** Can an Audio Connector integration be saved without a client secret? If not, the PoC still works, because signatures are logged and not verified, but the shared-module FR becomes a prerequisite for anything beyond the sandbox.
7. **Q-7** Padding: pad the trailing packet with `0xFF`, or send it short? (Padding chosen, as Pipecat does.)

## Consumer wiring (separate changes; do not gate this FR)

- **outcaller:** `server_incaller.py` adds `register_audiohook_websocket(app, session, api_key=<env value read by outcaller>)`. A `start-genesys.sh` starts ngrok and the server without the Twilio webhook. There is no `/incoming` step, because the WebSocket upgrade *is* the call. outcaller pins the new voice_runtime version.
- **CSAP:** a telephony-neutral hang-up seam, then `WS /genesys` on the supervisor (signature, probe, slot, busy as `disconnect` + `csap_status=busy`). These are separate CSAP FRs after the spike.

## Out of scope

- RFC 9421 signature verification, the nonce replay cache, and the shared `audiohook/` module (messages, sequencing, signature, pacer). These belong to the next voice_runtime FR.
- Re-basing VR-008 (the client) on shared code.
- `outputVariables` content and its contract with Architect flow owners; `outputVariables` on `closed`.
- `pause` / `resume` initiated by us; `reconnect`; `bot_turn_response` events; DTMF handling beyond logging.
- Mark options B and C.
- Concurrent calls on one `VoiceSession`, as on Twilio.
- CSAP, csap-black-box-tests and outcaller code.
- CI, hook and doctrine changes; a release tag or upload before C-4 is met.

## Frozen scope and conditions (judgement)

| Deliverable | Surface |
|---|---|
| D-1 | `voice_runtime/transports/audiohook_server.py` with the registered `/audiohook` transport |
| D-2 | One private STT-start helper under `voice_runtime/transports/` and the minimal `twilio_ws.py` call-site replacement |
| D-3 | `tests/test_vr011_audiohook_server.py` |
| D-4 | `README.md` AudioHook registration and experimental-limit documentation |
| D-5 | `pyproject.toml` minor version bump and `CHANGELOG.md` entry |
| D-6 | This FR's implementation record: witness map, commands, results, deviations, E-1 and later E-2 evidence |

| # | Condition | Status |
|---|---|---|
| C-1 | Fold R-1 to R-5 and the revised AC list before production edits | Done 2026-11-03 |
| C-2 | RED commit (AC-01) precedes GREEN | Done: RED `7bb9687`, GREEN `03aa406` |
| C-3 | Operator approves sandbox-only, dedicated-key exposure before E-2 | Approved 2026-11-03 (see Field evidence) |
| C-4 | No tag or publish until E-2 is recorded and passes | Open |
| C-5 | No surface listed as not authorized; adjacent work needs its own judged FR | Standing |

## Implementation record

**Status:** GREEN, docs and metadata done on `feat/vr-011-audiohook-server` (local; not pushed). E-1 run 2026-11-03: it found two defects, both fixed test-first. E-1 accepted by the owner, including the patched-client sub-run E-1c (see Field evidence). E-2 not started (pre-release, C-4).

**Commits**

| Step | Commit | Content |
|---|---|---|
| RED | `7bb9687` | `tests/test_vr011_audiohook_server.py`: 35 witnesses, committed with `SKIP=pytest`. RED run: 1 failed (AC-01 `find_spec`), 34 skipped on the missing module. |
| GREEN | `03aa406` | `audiohook_server.py`, `_audiohook_writer.py`, `_stt_lifecycle.py`, the `twilio_ws.py` call site (+2 −18). |
| Docs | `84b7fe0` | README section, CHANGELOG 0.4.0, `pyproject.toml` 0.4.0, first version of this record. |
| RED (E-1 #1) | `2ccc2d8` | `test_ac04_probe_through_real_fastapi_route`: real Starlette upgrade via `TestClient`. Failed with `WebSocketDisconnect` (HTTP 403). |
| GREEN (E-1 #1) | `4c489d1` | Import `WebSocket` at runtime so FastAPI can resolve the endpoint annotation. |
| RED (E-1 #2) | `5bc73d1` | `test_ac04_probe_nil_uuid[nil-conversation, nil-participant]`: 2 failed, 36 passed. The harness `push_open` gained a `participant_id` argument (additive). |
| GREEN (E-1 #2) | `b861e1e` | `_is_probe`: nil-UUID `conversationId` or `participant.id`, or JSON `null`. |
| Record | this commit | E-1 evidence and FR protocol-fact corrections. |

The test file was unchanged between the first RED and GREEN (`git diff 7bb9687 03aa406 -- tests/` is empty). Later test changes are only the two E-1 witnesses and the additive `push_open` argument.

Commit SHAs above are post-rebase. Pre-rebase SHAs, which appear in the RED/GREEN commit messages and in the judgement's "judged revision" field: `cb0d863`→`9b397e0`, `36d2af5`→`d3a5eec`, `a488354`→`7bb9687`, `119b692`→`03aa406`, `789130f`→`84b7fe0`, `d3415a9`→`2ccc2d8`, `37d6203`→`4c489d1`, `3a1a09a`→`5bc73d1`, `16526ae`→`b861e1e`.

**Witness map** (all in `tests/test_vr011_audiohook_server.py`, module-level `pytestmark = req("VR-011")`)

| AC | Tests |
|---|---|
| AC-01 | `test_ac01_audiohook_server_module_exists` and the RED run above |
| AC-02 | `test_ac02_registration_validation[*]`, `test_ac02_secrets_absent_from_logs` |
| AC-03 | `test_ac03_unauthorized[*]` |
| AC-04 | `test_ac04_probe`, `test_ac04_probe_nil_uuid[*]`, `test_ac04_probe_through_real_fastapi_route` |
| AC-05 | `test_ac05_call_open_binds_after_opened`, `test_ac05_secondary_factory_tee`, `test_ac05_unsupported_media` |
| AC-06 | `test_ac06_envelope_and_sequencing` |
| AC-07 | `test_ac07_sequence_fault[*]` |
| AC-08 | `test_ac08_inbound_rechunking` |
| AC-09 | `test_ac09_outbound_pacing[*]` |
| AC-10 | `test_ac10_single_writer_all_paths` |
| AC-11 | `test_ac11_byte_clock_marks`, `test_ac11_mark_without_audio` |
| AC-12 | `test_ac12_playback_evidence_logs_and_resolves_nothing` |
| AC-13 | `test_ac13_barge_in` |
| AC-14 | `test_ac14_bot_disconnect_with_close`, `test_ac14_bot_disconnect_timeout` |
| AC-15 | `test_ac15_peer_close`, `test_ac15_websocket_disconnect`, `test_ac15_receive_failure`, `test_ac15_send_failure`, `test_ac15_no_child_tasks_survive` |
| AC-16 | `test_ac16_import_does_not_load_twilio`, plus the unmodified Twilio, VR-003 and VR-007 suites in the full run |
| AC-17 | Full suite, ruff, wheel build and inspection below |
| AC-18 | README "Transport: AudioHook (Genesys Audio Connector) — experimental" |
| AC-19 | E-1 / E-2 below |
| AC-20 | This record |

**Commands and results** (Python 3.12, yamlgraph root venv)

- Focused: `python -m pytest tests/test_vr011_audiohook_server.py -q --no-cov` → 35 passed at `03aa406`; 38 passed at `b861e1e`.
- Full: `python -m pytest tests/ -q --no-cov` → 675 passed, 1 skipped at `03aa406`; 678 passed, 1 skipped at `b861e1e`. The 9 or 10 `RuntimeWarning: coroutine ... never awaited` warnings are attributed to older test modules (`test_nc170_*`, `test_nc260_*`, `test_session.py`, ...), and their count varies between identical runs. None comes from VR-011.
- `ruff check voice_runtime/` → all checks passed. `ruff format --check voice_runtime/transports/ tests/test_vr011_audiohook_server.py` → formatted.
- `pre-commit run --files voice_runtime/transports/*.py tests/test_vr011_audiohook_server.py` → all hooks passed, including the file-size gate and radon (no new function is grade C or worse).
- Artifact: `uvx --from build pyproject-build --wheel` → `voice_runtime-0.4.0-py3-none-any.whl`, 30 files. It contains `transports/audiohook_server.py`, `_audiohook_writer.py` and `_stt_lifecycle.py`. No `tests/`, `feature-requests/` or `.env*`. The `pyproject.toml` diff is the version line only, so no dependency is added.

**Deviations**

1. **D-1 is split across two files.** The first draft of `audiohook_server.py` had 517 lines, which fails the 450-line pre-commit gate. `_Writer` and `_Mark` moved to the private `voice_runtime/transports/_audiohook_writer.py`, which receives the clock as injected `now`/`sleep` callables. `audiohook_server.py` passes late-bound lambdas over its module-level `_now`/`_sleep`, so the test seam is unchanged. It re-exports `PACKET_BYTES` and `PACKET_INTERVAL_S` via `__all__`. The public surface is still only `register_audiohook_websocket`. The reviewer should confirm that this private module stays within D-1.
2. **Version 0.4.0, not 0.3.0.** 0.3.0 (VR-010) was released from sheikkinen/voice_runtime#10 while this work was in progress. The branch was rebased onto `903823c` (2026-11-03). The only conflict was the docs commit's `pyproject.toml` (kept `0.4.0`) and `CHANGELOG.md` (kept both entries: `0.4.0 - Unreleased` above `0.3.0`). After the rebase: full suite 678 passed, 1 skipped.
3. **Pre-commit is not installed as a git hook in this checkout.** The commits ran no hooks. The full hook set was run manually before each GREEN commit (all passed). The RED commits used `SKIP=pytest`, as planned, and no other hook was skipped by intent.
4. **E-1 found two defects that the fake-socket harness could not see.** (a) Every real upgrade got HTTP 403, because `WebSocket` was imported only under `TYPE_CHECKING` and FastAPI could not inject it. The fake socket bypassed FastAPI injection by capturing the handler directly. (b) Genesys probes carry the nil UUID, not JSON `null`, so a probe was bound as a call. The FR's protocol facts, glossary and lifecycle text are corrected in place and marked "corrected by E-1". The intent (a probe never binds) is unchanged.
5. **E-1 "audio both ways" needs a client patch.** The pinned reference client (`2871b01`, which is also upstream HEAD) treats every server binary message as fatal (`signalFatalError(415, 'Unexpected binary message')`). It predates the Audio Connector's bidirectional audio. E-1 was therefore run as three sub-runs (below). Whether E-1c's one-function local client patch satisfies the frozen E-1 wording was an owner decision. **Owner decision, 2026-11-03 (@sheikkinen): E-1c with the patched client is accepted as meeting E-1.** Bot-to-Genesys audio against real Genesys is still verified in E-2.

**Field evidence**

- **E-1, 2026-11-03.** Harness: the pinned client `purecloudlabs/audiohook-reference-implementation@2871b01`. Dependencies were installed from the public npm registry without the lockfile, which points at an internal Genesys mirror. It ran under `ts-node` with transpile-only and a pseudo-terminal (`script`), because the client needs a TTY. The server was a temporary FastAPI app (gitignored `tmp/e1_app.py`) that registers this transport on `ws://127.0.0.1:8799/audiohook` with one `VoiceSession` and a scripted bot thread (`wait_for_ws_connect`, `put_outbound_sync` of 2.0 s of μ-law tone, `send_mark_and_wait`, `request_disconnect`, `reset`). No production keys and no network exposure.

  | Run | Client | Result |
  |---|---|---|
  | E-1a probe | unmodified, `--connection-probe` | `opened` with `[PCMU 8000 external]` in 30 ms, never bound, `close`→`closed`, WS 1000. **Pass.** |
  | E-1a call | unmodified, tone, 3 s, no bot audio | `opened`, bound (`caller=+1-555-555-1234`), 2 ping/pong (RTT 3–20 ms), 24,000 B inbound = 150 frames of 160 B, `close`→`closed`, WS 1000, one `Call disconnected`. No protocol `error`; the client's sequence checks passed. **Pass.** |
  | E-1a wrong key | unmodified, wrong `X-API-KEY` | `disconnect(unauthorized, "unauthorized")` 503 ms after accept, never bound, socket closed. The client warns "closed unexpectedly in state CLOSING", because we close right after the disconnect instead of waiting for its `close` (AC-03 wording: "flush it, and close"). **Pass.** |
  | E-1b bot audio | unmodified | The client raised `error 415` on the first 1,600 B packet and sent `close(error)`. We logged `code=415`, answered `closed`, and disconnected once. This shows the client limitation and our error path. **Limitation, not a pass.** |
  | E-1c both ways | `onBinaryMessage` patched locally to count bytes instead of failing (patch below) | 10 packets × 1,600 B = 16,000 B (exactly 2.0 s), 200 ms apart, pongs interleaved. The byte-clock mark resolved after 2.044 s for 2.0 s of audio. `disconnect(completed)`→client `close`→`closed`, WS 1000. 20,800 B inbound = 130 frames. **Pass with a patched client.** |

  E-1c client patch (in `app/audiohook/src/client/clientsessionimpl.ts`, reverted after the run): the body of `onBinaryMessage` was replaced. Instead of `signalFatalError(415, ...)`, it adds `data.length` to a counter and logs it.
- E-2: not started. C-4 blocks any tag or upload containing this transport.

## Source references

- `voice_runtime/transports/twilio_ws.py:78` `register_voice_websocket`; STT start block after `event == "start"`; teardown in `finally`.
- `voice_runtime/session.py:30` `FRAME_BYTES`; `:181` `send_mark_and_wait`; `:218` `signal_mark_received`; `:227` `get_pending_mark`; `:233` `signal_ws_connected`; `:327` `request_disconnect`; `:350` `request_clear_buffer`.
- `tests/test_vr003_rest_first_call_end_31921.py:55-104`: fake-socket harness.
- Genesys AudioHook protocol reference: <https://developer.genesys.cloud/devapps/audiohook/protocol-reference>
- Genesys AudioHook security: <https://developer.genesys.cloud/devapps/audiohook/security>
- purecloudlabs/audiohook-reference-implementation @ `2871b01`: `app/audiohook/src/protocol/message.ts`, `app/audiohook/src/server/serversessionimpl.ts`, `app/src/authenticator.ts`.
- GenesysCloudBlueprints/audioconnector-server-reference-implementation @ `a167bb2`: `src/common/session.ts`, `src/protocol/message.ts`.
- pipecat-ai/pipecat @ `e03fa8d`: `src/pipecat/serializers/genesys.py` (1600-byte pacing comment).
