# VR-011 — AudioHook server transport (Genesys Audio Connector, minimal PoC)

**Priority:** MEDIUM
**Type:** Feature (new transport; Twilio transports unchanged)
**Status:** Draft — awaiting judgement
**Requested:** 2026-11-03
**Effort:** 1.5–2 days (library and fake-socket tests). The field spike is extra and depends on getting access to a Genesys org.
**Target release:** the next minor release after 0.3.0. A new public function counts as a feature (`reference/release-checklist.md`).
**First consumer:** outcaller's inbound app (`server_incaller.py`). It gains one registration line and a start script without the Twilio webhook. That is a separate change and does not gate this FR. Later consumers, CSAP (`WS /genesys` on the supervisor) and csap-black-box-tests, get their own FRs.
**Prior art:** `register_voice_websocket` (`voice_runtime/transports/twilio_ws.py:78`, the shape to mirror); NC-154 (transport intent: `request_disconnect`, `request_clear_buffer`); NC-236 (unique mark names); NC-260 (disconnect signalling); VR-004 (secrets kept out of `repr` and logs, and no reading of consumer environment variables); VR-007 (FR format); VR-008 (draft AudioHook **client**, unjudged, on branch `docs/vr-008-audiohook-fr`).
**Research:** [Dispositioned alternatives](#research-and-prior-art). The protocol facts it relies on are restated in [Protocol facts](#protocol-facts-this-fr-relies-on), with sources pinned to commits.
**Judgement:** _pending — the YAMLGraph judge adapter only; never this session_

## Glossary

- **AudioHook:** the WebSocket protocol, version 2, that Genesys Cloud uses to stream call audio to an external server.
- **Audio Connector:** the Genesys Architect action that uses AudioHook **in both directions**. The caller's audio comes to us, and our bot's audio goes back to the caller. Our use case.
- **Client / server:** Genesys is the **client**: it opens the WebSocket. We are the **server**. VR-008 is the opposite role (we act as Genesys).
- **Probe session:** a session Genesys opens when an integration is set up or tested. Its `open` has a null `conversationId`, and no caller is on the line.
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
| `open` | Contains `organizationId`, `conversationId` (null for a probe), `participant{id, ani, aniName, dnis}`, offered `media[]`, `language` and `inputVariables`. We must answer `opened` **within 5 s**. |
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
- **Failure path.** Wait until at least 500 ms have passed since accept, then send `disconnect{reason:"unauthorized"}`. Its `info` is generic and never says which check failed. Then close the socket. The session is not touched: no `signal_ws_connected`, no `call_sid`, no STT.
- `Signature` / `Signature-Input` are **not verified** in this FR. They are logged as present or absent, never by value. The PoC integration is configured without a client secret if Genesys allows it (open question Q-6). Verification belongs to the shared-module FR.

**Session lifecycle.**
- **Probe.** On `open` with a null `conversationId`: send `opened` with the chosen media, then answer the later `close` with `closed`. Never touch the session.
- **Call.** On `open` with a `conversationId`:
  1. Choose the offered PCMU 8000 `external` entry unchanged and send `opened` before any other work. If none is offered, send `disconnect{reason:"error"}` and close.
  2. Set `session.call_sid = conversationId` and `session.caller_number = participant.ani`.
  3. Call `session.signal_ws_connected(<Audiohook session id>)`. The session id doubles as `stream_sid`, which `request_clear_buffer` needs.
  4. Start the writer task and the STT lifecycle exactly as `twilio_ws` does today: factory, optional tee, `on_stt_ready`, then `stt.start(session.inbound)`. That block (`twilio_ws.py`, the code after `event == "start"`) moves into one private helper in `voice_runtime/transports/` that both transports call. It is moved, not changed: existing Twilio tests pass unmodified.
- **Inbound sequence check.** Each Genesys `seq` must be the previous one plus 1. Any other value: log at ERROR, send `disconnect{reason:"error"}`, and end the session (F-4).
- **Control messages.**
  - `ping` → `pong`; `update` → `updated`.
  - `paused`, `resumed`, `discarded` → log at INFO with their parameters.
  - `dtmf` → log at INFO.
  - `error` → log at WARNING with code and `retryAfter`.
  - `playback_started` / `playback_completed` → see Marks.
  - An unknown `type` → log at WARNING and ignore.

**Inbound audio.**
- Binary frames are re-chunked into exact `FRAME_BYTES` (160-byte) frames, keeping the remainder for the next frame.
- Each full frame goes to `session.put_inbound` and `session.tap_caller`. Consumers keep seeing Twilio-sized frames (F-7).

**Single writer and pacing (F-4, F-8).**
- One writer task owns every `send_text` / `send_bytes` on the socket.
- Text messages are written in the order they are queued, with `seq` assigned at write time and `clientseq` set to the last Genesys `seq` seen.
- **Outbound audio:**
  - Drain `session.outbound` into a pacer buffer and send it as **1600-byte** binary messages, at most one per 200 ms on the loop clock.
  - A trailing partial packet is padded with `0xFF` to 1600 bytes when the queue has been empty for one packet interval, or when a mark is waiting on it.
  - Text messages never wait behind audio for more than one packet interval.

**Marks: byte-clock emulation, option A.**
- When the writer takes a mark from `session.get_pending_mark()`, it first moves everything currently in `session.outbound` into the pacer buffer.
- The mark's target is the total outbound byte count at that moment, including padding.
- The mark resolves (`session.signal_mark_received(name)`) once the byte clock has reached the target **and** `mark_latency_s` has elapsed since the packet containing the target was sent.
- Marks resolve in the order they were queued.
- **Evidence for the spike.** Every `playback_started` / `playback_completed` is logged at INFO with:
  - its `position`;
  - the byte clock in seconds;
  - the name of the most recently resolved mark;
  - the time since that mark resolved.

  These events never resolve a mark in this FR. Options B and C (playback events as barrier or confirmation) wait for that evidence.

**Barge-in (`request_clear_buffer`, NC-154).**
- Drop the pacer buffer and everything in `session.outbound`.
- Send `event{entities:[{type:"barge_in",data:{}}]}`.
- Resolve every pending mark at once. This mirrors Twilio, which echoes pending marks after `clear`.

**Ending.**
- **Bot ends.** On `session.request_disconnect()` (`_disconnect_requested`):
  - send `disconnect{reason:"completed", outputVariables:{}}`;
  - wait up to 10 s for Genesys's `close`, answer `closed`, then close the socket;
  - if no `close` arrives, close the socket after the wait.
- **Caller ends.** On Genesys `close`: answer `closed{}` immediately, then `session.signal_disconnected()`.
- **Every exit path** calls `session.signal_disconnected()` (NC-260 Gap B semantics), stops STT and cancels the writer — the same teardown order as `twilio_ws`. This includes `WebSocketDisconnect`, a send failure, a sequence error and an authentication failure after a session was bound.

**Import path.** Consumers import `voice_runtime.transports.audiohook_server.register_audiohook_websocket`, the same way they import `twilio_ws` today. `voice_runtime/transports/__init__.py` stays empty.

### 2. Dependencies

None. The transport uses FastAPI/Starlette's `WebSocket` (`receive()` gives text or bytes) and the stdlib `hmac`. `pyproject.toml` changes only the version. The base install and `import voice_runtime` are unchanged.

### 3. Documentation

- README: a short "AudioHook (Genesys Audio Connector)" section with the registration call.
- The section states the PoC limits:
  - no signature verification;
  - byte-clock marks;
  - empty `outputVariables`;
  - one call per `VoiceSession`, as on Twilio.
- CHANGELOG entry for VR-011.

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

- [ ] **AC-01:** A test-only behavioural RED commit adds the test file. Its failures are assertions or a missing attribute on `voice_runtime.transports.audiohook_server`, not collection or fixture errors.
- [ ] **AC-02:** Registration validation.
  - An empty `api_key` or a negative `mark_latency_s` raises `ValueError` before a route is added.
  - The key value is absent from the exception text and from all log records captured during the whole test file.
- [ ] **AC-03:** Authentication failure.
  - Applies to a wrong `X-API-KEY` and to an `organization_id` mismatch, in the header and in `open`.
  - The handler sends exactly one text message, `disconnect` with `reason "unauthorized"` and generic `info`, no earlier than 500 ms after accept, then closes.
  - The session's `call_sid`, `stream_sid` and STT stay untouched.
- [ ] **AC-04:** Probe.
  - On `open` with null `conversationId`, the handler answers `opened` (`seq 1`, `clientseq 1`, the offered PCMU media unchanged, `parameters` present) and later answers `close` with `closed`.
  - `signal_ws_connected` is never called and no STT factory is invoked.
- [ ] **AC-05:** Call open.
  - On `open` with a `conversationId`, `opened` is the first message sent, before the STT factory is invoked.
  - `session.call_sid == conversationId`, `session.caller_number == participant.ani`, `session.stream_sid ==` the AudioHook session id.
  - The STT lifecycle runs: factory, optional tee and `on_stt_ready`.
  - An `open` offering no PCMU 8000 entry gets `disconnect{reason:"error"}`.
- [ ] **AC-06:** Envelope and sequencing.
  - Across a scripted session (`open`, `ping`, `update`, audio, `close`), every text message we send has `version "2"` and `id` = the session id.
  - `seq` is 1, 2, 3… with no gap or duplicate.
  - `clientseq` equals the last Genesys `seq` received.
  - `parameters` is always present, and `position` is never present.
  - `ping` gets `pong` and `update` gets `updated`.
- [ ] **AC-07:** Inbound sequence gap. A Genesys `seq` that skips or repeats causes one ERROR log, a `disconnect{reason:"error"}` and `signal_disconnected()`.
- [ ] **AC-08:** Inbound audio.
  - Binary frames of 1, 159, 160, 333 and 4000 bytes reach `session.inbound` as consecutive 160-byte frames with the same byte content, carrying the remainder across frames.
  - `tap_caller` sees the same bytes.
- [ ] **AC-09:** Outbound pacing.
  - For 10 s of queued TTS audio in 160-byte chunks, every binary message is exactly 1600 bytes, including a final packet padded with `0xFF`.
  - No two binary messages are less than one packet interval apart, so there are never more than 5 per second at the production interval.
  - The concatenated payload equals the input followed only by `0xFF` padding.
- [ ] **AC-10:** Single writer. With audio streaming and a `ping` arriving mid-stream, all sends happen from one task (asserted via the fake socket's recorded task identity), and `pong` is sent within one packet interval.
- [ ] **AC-11:** Byte-clock marks.
  - `send_mark_and_wait` called from a worker thread after N bytes of audio returns only after the packet containing byte N has been sent **and** `mark_latency_s` has elapsed. Never earlier.
  - Two marks resolve in queue order.
  - A mark queued with no audio before it resolves after `mark_latency_s`.
- [ ] **AC-12:** Playback evidence. `playback_started` / `playback_completed` produce one INFO record each, containing the event `position` and the byte-clock seconds. They resolve no mark.
- [ ] **AC-13:** Barge-in. `request_clear_buffer()` during playback:
  - sends no further audio from before the clear;
  - sends exactly one `event` whose single entity is `barge_in`;
  - resolves every pending mark.
- [ ] **AC-14:** Bot ends. `request_disconnect()`:
  - sends `disconnect{reason:"completed", outputVariables:{}}`;
  - answers Genesys's following `close` with `closed`, then closes the socket;
  - without a `close`, closes the socket after the wait. The wait is a module constant, set to 10 s in production and patched in the test.
- [ ] **AC-15:** Caller ends. Genesys `close{reason:"end"}` gets `closed` and `signal_disconnected()`. `WebSocketDisconnect` and a send failure each call `signal_disconnected()` exactly once. Teardown stops STT and cancels the writer in every case.
- [ ] **AC-16:** Twilio unchanged.
  - The existing `tests/test_twilio_ws.py`, VR-003 and VR-007 tests pass without modification after the STT helper extraction.
  - Importing `voice_runtime.transports.audiohook_server` in a fresh interpreter does not put `twilio` or `voice_runtime.transports.twilio_ws` in `sys.modules`. The shared STT helper therefore lives outside `twilio_ws.py`.
- [ ] **AC-17:** Release hygiene.
  - The full offline suite passes, and `ruff check voice_runtime/` is clean.
  - No new runtime dependency.
  - The version is bumped minor and CHANGELOG records VR-011.
  - The README section states the PoC limits.
  - The wheel builds and contains no `feature-requests/`, `tests/` or `.env*`.
  - No upload or tag is part of this criterion (`reference/release-checklist.md`).
- [ ] **AC-18:** The FR records RED/GREEN commits, the witness map from AC to test, suite results and any deviation.

**Field evidence (recorded in this FR once; does not gate the release, per the unit-test boundary):**

- **E-1:** The purecloudlabs reference **client** (Node CLI, streaming a WAV) completes a session against outcaller hosting this transport: open, `opened`, audio both ways, close, `closed`. No `error` message is received.
- **E-2:** A Genesys sandbox Architect flow with Audio Connector reaches outcaller over ngrok on port 443. The record includes the conversation id, transcript excerpts, the number of `playback_*` pairs per bot turn with their timing against the byte clock, any 429/409/413 errors, behaviour after `barge_in`, and whether audio sent just before `disconnect` was heard.

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

## Implementation record

_Empty until enforcement._

## Source references

- `voice_runtime/transports/twilio_ws.py:78` `register_voice_websocket`; STT start block after `event == "start"`; teardown in `finally`.
- `voice_runtime/session.py:30` `FRAME_BYTES`; `:181` `send_mark_and_wait`; `:218` `signal_mark_received`; `:227` `get_pending_mark`; `:233` `signal_ws_connected`; `:327` `request_disconnect`; `:350` `request_clear_buffer`.
- `tests/test_vr003_rest_first_call_end_31921.py:55-104`: fake-socket harness.
- Genesys AudioHook protocol reference: <https://developer.genesys.cloud/devapps/audiohook/protocol-reference>
- Genesys AudioHook security: <https://developer.genesys.cloud/devapps/audiohook/security>
- purecloudlabs/audiohook-reference-implementation @ `2871b01`: `app/audiohook/src/protocol/message.ts`, `app/audiohook/src/server/serversessionimpl.ts`, `app/src/authenticator.ts`.
- GenesysCloudBlueprints/audioconnector-server-reference-implementation @ `a167bb2`: `src/common/session.ts`, `src/protocol/message.ts`.
- pipecat-ai/pipecat @ `e03fa8d`: `src/pipecat/serializers/genesys.py` (1600-byte pacing comment).
