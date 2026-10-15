# VR-008 — AudioHook client transport (Genesys AudioHook v2, Audio Connector role)

**Priority:** MEDIUM
**Type:** Feature (new transport; Twilio transports unchanged)
**Status:** Draft — awaiting judgement
**Requested:** 2026-10-15
**Effort:** 2–3 days (library + fake-server tests + stand-alone smoke script)
**Target release:** 0.1.16
**First consumer:** stand-alone SSC+ Line smoke script (`scripts/ssc_line_smoke.py`, this repo, not packaged). Second consumer: CSAP SSC-* test cases (separate CSAP FR).
**Prior art:** NC-271 (`FakeWsBridge`, the WebSocket-client precedent), NC-236 (unique marks), NC-260 (disconnect signalling), VR-004 (credential model hygiene), VR-007 (FR format).
**Research:** [Dispositioned alternatives](#research-and-prior-art)
**Judgement:** _pending — the YAMLGraph judge adapter only; never this session_

## Glossary

- **AudioHook**: the Genesys Cloud WebSocket protocol, version 2. The *client* (normally Genesys Cloud) opens the connection, streams caller audio and owns the session. The *server* (here SSC+) answers.
- **Audio Connector**: the AudioHook feature where the server is a bot. It sends audio back as binary frames, and the bot's audio is played to the caller.
- **SSC+ Line**: JST's symptom-checker bot. It is exposed only as an AudioHook server at `wss://api.dev.ssc-plus.com/v1/line/{connectorId}`. It has no phone number.
- **Caller side**: in this FR, `VoiceSession.outbound` is what *we* say (a synthetic caller), and `VoiceSession.inbound` is what *we* hear (the SSC+ bot).

## Problem and ideal result

JST's SSC+ sub-bot can only be reached as an AudioHook server, through a Genesys Audio Connector. voice_runtime only implements one media transport: the Twilio Media Streams *server* (`twilio_ws.register_voice_websocket`). Nothing in our stack can open an AudioHook session. So neither a smoke test nor a CSAP test case can talk to SSC+ without a Genesys tenant, which JST would have to provision and we cannot control.

**Ideal result:** voice_runtime ships an AudioHook *client* transport that drives an unchanged `VoiceSession`. A consumer that already speaks through `VoiceSession` (TTS writes to `outbound`, marks via `send_mark_and_wait`, STT reads `inbound`) can talk to any AudioHook Audio Connector server without code changes. It gets the server's `outputVariables` at the end. A stand-alone smoke script proves the path against SSC+ dev, independent of CSAP and of csap-black-box-tests.

## Proposed solution

### 1. `voice_runtime/transports/audiohook_client.py` (new)

- **`AudioHookConfig`** (pydantic, frozen):
  - `uri`, `organization_id`, `language`.
  - `api_key` and `client_secret`, both `repr=False`, as in the VR-004 `TwilioRegion` hygiene.
  - `input_variables: dict[str, str]`.
  - The library reads **no environment variables**. The consumer supplies every value.
- **`sign_upgrade_request(config, *, session_id, correlation_id, created, nonce) -> dict[str, str]`**: a pure function, stdlib `hmac`/`hashlib` only.
  - Builds the RFC 9421 signature base over `@request-target`, `@authority`, `audiohook-organization-id`, `audiohook-session-id`, `audiohook-correlation-id`, `x-api-key`.
  - Signature parameters: `keyid` = API key, `nonce`, `alg="hmac-sha256"`, `created`, `expires`.
  - The HMAC key is the base64-decoded client secret.
  - Returns all upgrade headers: `Audiohook-*`, `X-API-KEY`, `Signature`, `Signature-Input`.
  - `created`/`nonce` are injectable so the Genesys example can be reproduced byte for byte.
- **`run_audiohook_session(session, config, *, conversation_id, participant, sleep=asyncio.sleep, ping_interval=5.0) -> AudioHookOutcome`**: one coroutine that owns one session from connect to TCP close.
  - **Connect.** Use `websockets.asyncio.client.connect(uri, additional_headers=signed)`. The `websockets` import is lazy, as in `mock_bridge.py:151`. An HTTP refusal (for example 401, 403, 404) ends the session with `AudioHookOutcome(reason="http_<status>")` and calls `session.signal_disconnected()`.
  - **Open transaction.** Send `open` with:
    - `version:"2"`, `seq:1`, `serverseq:0`, `position:"PT0S"`, and `id` = session id;
    - parameters: `organizationId`, `conversationId`, `participant`, `media:[{type:audio, format:PCMU, channels:[external], rate:8000}]`, `language`, `inputVariables`.

    Wait up to 5 s for `opened`. Send no audio before `opened`. On `opened`, call `session.signal_ws_connected(stream_sid=<session id>)`. `call_sid` is set to `conversationId`.

    A `disconnect` received instead of `opened` is a rejection, not an error. It is recorded in the outcome.
  - **Sequence numbers.** The client's `seq` increases by exactly 1 per text message. Every client message carries `serverseq` (the last server `seq` seen) and `position`. A server `seq` that is not last+1 is a protocol error: the client closes and records `reason="protocol_error"`.
  - **Caller audio (send pump).** Caller audio goes out in 20 ms PCMU frames (`FRAME_BYTES = 160`, `session.py:30`), paced in real time on the loop clock.
    - When `session.outbound` is empty, the pump sends μ-law silence (`0xFF`). A real call leg streams continuously, and SSC+ voice activity detection (VAD) sees an unbroken timeline.
    - `position` is computed from the count of samples sent as an exact ISO 8601 duration. It is never rounded to whole milliseconds (Genesys requirement).
  - **Marks.** A mark from `session.get_pending_mark()` is resolved with `session.signal_mark_received(name)` once every outbound frame queued before it has been *paced out*. This keeps `send_mark_and_wait` meaning "the caller has finished saying it". AudioHook has no wire-level mark.
  - **Clear buffer.** `request_clear_buffer` drops outbound frames that have not been sent yet. This is local only, because there is no AudioHook equivalent.
  - **Bot audio (receive + playout).** A server may send audio in bursts: at most one message every 200 ms, each up to 64,000 bytes. Received bytes go into a playout buffer, which is drained in real time as 160-byte frames into `session.put_inbound()`. This means STT, VAD and recorders see the bot at listening speed, as a phone would play it.
    - Send `playback_started` when the buffer goes from empty to non-empty.
    - Send `playback_completed` when it drains. The exact semantics are pending Erno Q3 (see Open questions).
  - **Control messages:**
    - Ping every `ping_interval` seconds after `opened`, and two pings about 1 s after open (Genesys guidance). Measure round-trip time from `pong` and log it.
    - Server `pause` → stop sending audio and reply `paused`. Server `resume` → reply `resumed` and continue.
    - `event` → log at DEBUG.
    - Unknown type → log and ignore.
  - **Ending.**
    - Server `disconnect` → record `reason` and `outputVariables`, start a `close` transaction, wait up to 10 s for `closed`, then close the WebSocket.
    - `session.request_disconnect()` (the consumer hangs up) → the same `close` transaction. No `outputVariables` are expected.
    - Every exit path calls `session.signal_disconnected()` (NC-260 Gap B semantics).
  - **Return value.** `AudioHookOutcome(reason, output_variables: dict[str, str], session_id, server_disconnect_info, closed_cleanly: bool)`.
  - **No reconnection.** Genesys documents reconnection only for Monitor and Transcription Connector, not for Audio Connector.
- **Logging.** The API key, client secret, `Signature` and `Signature-Input` never reach a log record or a `repr`.

### 2. `pyproject.toml`

- New extra: `audiohook = ["websockets>=14"]`. The floor is the first release with the `websockets.asyncio.client` API that the code uses.
- The base install is unchanged, and `import voice_runtime` must keep working without `websockets`.

### 3. `scripts/ssc_line_smoke.py` (new, stand-alone, not in the wheel)

A CLI that depends only on installed `voice-runtime[audiohook,azure]` (or `[elevenlabs]`) and `httpx`. It does not depend on CSAP, yamlgraph or csap-black-box-tests.

1. **Configuration.** Credentials come only from the environment: `SSC_LINE_URI`, `SSC_LINE_API_KEY`, `SSC_LINE_CLIENT_SECRET`, `SSC_ORGANIZATION_ID`, `SSC_TENANT_API_KEY`, `SSC_API_BASE`. Scenario values come from flags: `--age 45 --sex female --symptoms kuume --language fi --answers answers.txt --tts azure|elevenlabs --out DIR`.
2. **Session.** Build a `VoiceSession` and a TTS provider via `create_tts`. Run `run_audiohook_session` with `conversation_id` set to a fresh UUID.
3. **Turn policy.** After each `playback_completed`, wait `--reply-delay` (default 0.6 s), synthesise the next line of `--answers` (default `ei`), and finish with `send_mark_and_wait`. When the answers run out, keep sending silence until the server disconnects or `--max-seconds` is reached; at the timeout, `request_disconnect`.
4. **Recording.** Write `sent.ulaw`, `received.ulaw` and `messages.jsonl` (every protocol message except headers) to `--out`, so a run can be listened to and audited.
5. **Result check.** If `ssc_session_id` is present, `GET {SSC_API_BASE}/v1/sessions/{id}` with `Authorization: Bearer $SSC_TENANT_API_KEY`. Print `ssc_status`, `ssc_triage`, `ssc_urgency`, the API `status` and `result.triage_code`.
6. **Exit code.**
   - 0 when `ssc_status == "result"`, the API status is `completed`, and `triage_code == ssc_triage`.
   - 2 when the session completed with a non-`result` status.
   - 1 for transport or authentication failures.
7. **`--dry-run URI`** runs against a local AudioHook server with no Customer API call. The test suite uses this to execute the script end to end.

### Not changed

- `VoiceSession`: no new fields and no changed signatures. Outputs travel in `AudioHookOutcome`, not on the session.
- `twilio_ws.py`, `twilio_call.py`, `mock_bridge.py`, and the STT/TTS providers.
- CSAP. Its pins (`pyproject.toml:22`, `requirements-deploy.txt:5`) move only in the consumer FR.

## Research and prior art

### Probes run (2026-10-15)

| Probe | Result |
|-------|--------|
| `GET https://api.dev.ssc-plus.com/v1/health` | `200 {"status":"ok"}` |
| Unsigned WebSocket upgrade to `/v1/line/csap-probe` | `404`. It is unresolved whether this is the authentication refusal or a wrong path. The live AC (AC-16) settles it. |
| Genesys example signature (session-walkthrough page: API key `SGVsbG8s…`, secret `TXlTdXBl…`, `created=1641013200`, nonce `VGhpc0lzQVVuaXF1ZU5vbmNl`) recomputed with stdlib `hmac` | `NZBwyBHRRyRoeLqy1IzOa9VYBuI8TgMFt2GRDkDuJh4=`, which equals the published value. The recipe needs no third-party signing library. |
| `websockets` in the voice_runtime dependencies | Absent. `mock_bridge.py:151` imports it lazily, and it is present only as 15.0.1 in the CSAP venv. Hence the new extra. |
| Genesys reference implementation (`purecloudlabs/audiohook-reference-implementation`, MIT, TypeScript) | Has a CLI **test client** that streams a 1 kHz tone or a WAV file and exits when the WAV ends. It has no turn-taking and does not consume the server's audio. |

### Alternatives

| # | Alternative | Disposition |
|---|-------------|-------------|
| A | AudioHook client as a voice_runtime transport driving `VoiceSession` | **Chosen.** One implementation serves the smoke script now and CSAP test cases next. Consumers keep their TTS/STT/mark code. |
| B | Genesys PSTN route provisioned by JST (we dial a number) | Not chosen for phase 1. It depends on JST provisioning a Genesys tenant (Erno Q6). It would still be the most production-like route later. |
| C | Twilio call → our bridge → AudioHook | Rejected. It needs the same AudioHook client plus PSTN cost and a second media hop. |
| D | Text persona tester (CSAP NC-340) | Impossible. SSC+ Line is audio-only. |
| E | Use the Genesys reference TS client as the smoke test | Rejected as the vehicle: it plays a fixed WAV with no turn-taking, so it cannot answer SSC+ questions, and it is Node rather than voice_runtime. **Adopted as a day-0 probe:** once keys arrive, run it with a silence WAV to confirm authentication and the open transaction independently of our code. If our client and theirs disagree, theirs is the oracle. |
| F | Extend `twilio_ws` with an AudioHook mode | Rejected. The roles are inverted (Twilio WS is a server, AudioHook is a client), and the framing differs (JSON+base64 versus binary frames plus text control). |

**Is this a graph? No.** This is a real-time media transport with sub-second pacing. There is no decision or LLM step.

### Precedents in this repo

| Precedent | What it shows | What it does **not** show |
|-----------|---------------|---------------------------|
| `transports/mock_bridge.py` `FakeWsBridge` (NC-271) | voice_runtime already contains a WebSocket *client* (lazy `websockets` import, l.151). It impersonates Twilio toward our own `/voice` server: connected/start events and mark echo. | It exchanges **no audio** and drives no `VoiceSession`. It is not a model for pacing or playout. |
| `transports/twilio_ws.py` pump tasks (`send_audio`, `send_marks`, `watch_disconnect`, `send_clears`, l.112–197) | The task-per-concern shape this transport copies, and the `VoiceSession` contract a transport must honour. | Twilio does real-time playout itself; AudioHook leaves pacing to the client. |
| `session.py` `send_mark_and_wait` (l.181), `request_disconnect` (l.327), `request_clear_buffer` (l.350) | Transport-intent APIs that are already provider-agnostic. | — |
| VR-004 `TwilioRegion` | `repr=False` credential fields; the library never reads env. | — |

## Acceptance criteria

All unit and integration tests carry `@pytest.mark.req("VR-008")` and live in `tests/test_vr008_audiohook_client.py`. The fake server is a `websockets.serve` instance on `127.0.0.1` inside the test, which **independently recomputes** the signature from received headers.

- **AC-01 RED.** A commit containing the failing tests alone (`SKIP=pytest`) precedes the implementation commit.
- **AC-02 Signing vector.** `sign_upgrade_request` with the Genesys example inputs returns `Signature: sig1=:NZBwyBHRRyRoeLqy1IzOa9VYBuI8TgMFt2GRDkDuJh4=:` and a `Signature-Input` byte-equal to the published one.
- **AC-03 Authentication.**
  - The fake server accepts a correctly signed upgrade.
  - When it refuses with 401, the outcome is `reason == "http_401"`, `session.is_disconnected` is true, and no exception escapes.
  - When it refuses with 404, the outcome is `"http_404"`.
- **AC-04 Open.** The server observes an `open` whose fields equal the Proposed-solution list, then **zero binary frames until it sends `opened`**. `session.wait_for_ws_connect` resolves only after `opened`.
- **AC-05 Sequence.**
  - Client `seq` values are 1, 2, 3, … with no gaps.
  - Every client message's `serverseq` equals the last server `seq`.
  - A server `seq` gap yields `reason == "protocol_error"` and a close.
- **AC-06 Pacing and silence.** Tests use an injected `sleep` / fake clock.
  - 1 s of outbound audio is sent as 50 × 160-byte frames on a 20 ms schedule.
  - With an empty outbound queue, the frames are `0xFF` silence.
  - `position` after N samples equals `PT{N/8000}S` exactly, with fractional digits preserved (tested for N = 1 and N = 8001).
- **AC-07 Playout.**
  - One 8,000-byte server burst becomes 50 `put_inbound` frames of 160 bytes on a 20 ms schedule.
  - `playback_started` is sent once at the start and `playback_completed` once on drain.
- **AC-08 Marks.** `send_mark_and_wait` returns only after every frame enqueued before it has been sent. A mark issued while 0.5 s of audio is queued resolves no earlier than the 25th frame.
- **AC-09 Control.**
  - Server `pause` → client sends `paused` and no binary frames until `resume` → `resumed`.
  - Client pings start after `opened`, and a `pong` is consumed without error.
  - An unknown message type is ignored.
- **AC-10 Server disconnect.**
  - Server `disconnect{reason:"completed", outputVariables:{ssc_status:"result", ssc_triage:"6", …}}` → client `close` → server `closed`.
  - The outcome carries those exact `outputVariables` and `closed_cleanly`.
  - `session.is_disconnected` is true.
- **AC-11 Client hangup.**
  - `session.request_disconnect()` → `close` → `closed` → WebSocket closed.
  - When the server never answers `closed`, the WebSocket still closes after 10 s (fake clock) with `closed_cleanly == False`.
- **AC-12 Secret hygiene.** Over a full fake session at DEBUG level, `caplog` contains neither the API key, the secret, nor the signature value. `repr(config)` contains neither.
- **AC-13 Isolation.**
  - In a subprocess with `websockets` made unimportable, `import voice_runtime` and `import voice_runtime.transports.twilio_ws` succeed.
  - Calling `run_audiohook_session` raises an `ImportError` naming the `audiohook` extra.
  - The existing Twilio tests pass unmodified.
- **AC-14 Smoke script executed.** A test runs `scripts/ssc_line_smoke.py --dry-run ws://127.0.0.1:<port>` with the voice_runtime mock TTS (NC-267) against the fake server. The fake server asks two "questions" (audio bursts) and then disconnects with `outputVariables`. The test asserts:
  - two answers were sent;
  - `sent.ulaw`, `received.ulaw` and `messages.jsonl` exist and are non-empty;
  - exit code 0.

  This exercises the script through its real entry point, not only its helpers.
- **AC-15 Release mechanics.**
  - `pytest tests/` is green.
  - The version is 0.1.16, and a CHANGELOG entry is added.
  - `python -m build` succeeds, and `twine check` passes.
  - A clean venv install of the wheel works with and without `[audiohook]`.
  - **No upload** without explicit human approval.
- **AC-16 Live evidence (one-time, recorded in the Implementation record, not a unit test).** This is gated on JST keys and on Erno Q1–Q3.
  1. Day-0 probe: the Genesys reference client, given the SSC+ dev credentials, completes `open`/`opened`.
  2. `ssc_line_smoke.py --age 45 --sex female --symptoms kuume` against SSC+ dev exits 0.
  3. Record the outcome `reason`, `ssc_status`, `ssc_triage`, the API `status` and `triage_code`, the session duration, and the number of turns.
  4. Listen to `received.ulaw` and note any clipped or overlapping turns.

  The release (AC-15) may precede AC-16. Until AC-16 is recorded, the status stays "Implemented — live evidence pending".

## Open questions (for JST, Erno Vuori; block AC-16 only)

1. **Organisation ID.** What `organizationId` / `Audiohook-Organization-Id` should a non-Genesys client send? Is any UUID accepted?
2. **Non-Genesys client.** Is a non-Genesys AudioHook client acceptable to JST for dev testing, and are there rate or volume limits?
3. **Playback events.** Does SSC+ rely on `playback_started` / `playback_completed` to know when the caller has heard the end of a prompt, given that it "listens from its last word"? If it ignores them, AC-07's events are harmless but untested against reality.
4. **The 404.** Is the 404 on an unsigned upgrade the intended authentication refusal?

## Implementation record

_Empty until enforcement._

## Consumer wiring (separate changes; do not gate this FR)

- **CSAP.**
  - Bump the pins to `voice-runtime[azure,elevenlabs,audiohook]==0.1.16`.
  - Add SSC-* test cases that drive `run_audiohook_session` and the SSC+ Customer API: SSC-01 to SSC-04 first, per `docs/plan-jst-voicebot-integration.md`.
  - Store the credentials as a Secret Manager entry, not in `.env` committed anywhere.
- **csap-black-box-tests.** Not involved. Its bash `run-test.sh --phone` places real PSTN calls, and SSC+ has no number.

## Out of scope

- L16 media, stereo (`external`+`internal`) offers, and server-side AudioHook.
- Reconnection, `discardTo`, and `update` messages from the client side.
- `dtmf`, `barge_in`, and `bot_turn_response` events (logged only).
- STT of SSC+ audio in the smoke script. The recording is kept for listening; transcription belongs to the CSAP test cases.
- Phase 2 (a live CSAP call handed to SSC+) and its safety FR.

## Source references

- `voice_runtime/session.py:30` (`FRAME_BYTES`), `:58` (`VoiceSession`), `:123` (`put_inbound`), `:181` (`send_mark_and_wait`), `:327` (`request_disconnect`), `:350` (`request_clear_buffer`).
- `voice_runtime/transports/twilio_ws.py:112-197`: pump tasks.
- `voice_runtime/transports/mock_bridge.py:151`: lazy `websockets` import.
- `voice_runtime/tts.py:6` (`create_tts`), `voice_runtime/stt.py:30` (`create_stt`).
- `pyproject.toml` `[project.optional-dependencies]`.
- Genesys AudioHook docs: session walkthrough (Establishing Connection example; Audio Streaming; Audio Streaming from the Server; Close Transaction), protocol reference, and security / client authentication.
- SSC+ Line Integration Guide 0.6.3; SSC+ API Reference (`GET /v1/sessions/{id}`).
