# Changelog

## 0.1.11 - 2026-08-07

### Fixed

- **VR-002** (issue #2): Twilio REST requests are now bounded by an explicit HTTP timeout. `send_sms`, `initiate_outbound_call`, `hangup_call`, and `list_recent_calls` build their client through `transports/_twilio_client.build_twilio_client`, which attaches a `TwilioHttpClient` with `TWILIO_HTTP_TIMEOUT` seconds (default `15`). The SDK's default client applies no request timeout, so a network path that silently drops packets blocked a synchronous call-path request for the OS TCP timeout (~2 min) — on the consumer's call-teardown path that freezes worker teardown and starves the pool. Credential pre-checks are unchanged.

## 0.1.10 - 2026-08-06

### Added

- **NC-395/NC-428** (ninchat_voice): `list_recent_calls(lookback_s)` in `transports.twilio_call` — read-only CDR fetch for the call-record reconciler. Returns `[{"call_sid", "status", "start_time"}]` for inbound calls to our number within the lookback window; `[]` without credentials. Ships the function the ninchat_voice reconciler has imported since NC-395 (the consumer merged against an unreleased checkout; every deployment logged an ImportError per tick — NC-428).

## 0.1.9 - 2026-07-10


### Added

- **NC-362** (ninchat_voice): `hangup_call(call_sid)` in `transports.twilio_call` — REST-boundary call termination. The consumer's supervisor reaper must end a stuck call without routing through the wedged worker; Twilio completes the call and closes the media WS from its side. Raises `RuntimeError` without credentials.

## 0.1.8 - 2026-06-12

### Fixed

- **NC-340**: STT audio feed loop now survives a reconnect. Previously an agent TTS turn longer than 10s triggered a Scribe reconnect that raced the audio feeder; the feeder hit consecutive `send()` failures on the torn-down socket, broke permanently, and was never restarted — leaving STT permanently deaf for the rest of the call. `_connect()` now guards the socket swap with a `_reconnecting` flag and ensures the feed task is alive before returning; `_feed_audio` treats send failures during a deliberate reconnect as transient.

## 0.1.4 - 2026-05-11

### Changed

- Release a fresh PyPI artifact for deployments that consume `voice-runtime` as a normal package dependency.

## 0.1.3 - 2026-05-11

### Fixed

- Mock calls now honor routed `<Stream url="...">` TwiML responses so supervisor-owned `/voice/{route_token}` WebSocket endpoints work in local E2E tests.
- Mock text relay now posts sideband text to the matching routed `/test/inject/{route_token}` endpoint after call initiation.

## 0.1.2 - 2026-05-11

### Added

- `build_route_stream_xml()` for tokenized Twilio Media Streams endpoints owned by supervisors that route `/voice/{route_token}`.

### Fixed

- Keeps routed Twilio stream XML generation inside the `voice_runtime` transport boundary so application consumers do not need provider wire-protocol literals.

## 0.1.0 — 2026-05-10

Initial public release under MIT license.

### Core

- `VoiceSession` dataclass — provider-agnostic call session coordinator. Manages audio queues, mark synchronization, disconnect signaling, and optional audio monitoring. Does not own the event loop; the transport layer provides one via `set_loop()`.
- `MissingStreamUrlError`, `CallNotAnsweredError`, `CallHangupError` — typed exceptions for call lifecycle failures.

### Providers

- `SttProvider` / `TtsProvider` — structural Protocol interfaces. Consumers depend on the protocol, not a concrete class.
- `PersistentSttSession` (ElevenLabs Scribe Realtime) — one WebSocket per call lifetime. Echo discard, reconnect with exponential backoff, barge-in via `set_speaking()`.
- `ElevenLabsTTS` — streams text → ElevenLabs Flash v2.5 → ffmpeg (MP3 → μ-law 8kHz) → outbound queue. Barge-in via `stop_event`.
- `AzurePersistentStt` — Azure Speech SDK continuous recognition. Echo discard window, silence timeout.
- `AzureTTS` — Azure Speech SDK synthesis → μ-law 8kHz.
- `SttTee` — dual-provider fan-out. Primary drives `on_committed`; secondary receives same frames for logging only.
- `MockStt` / `MockTts` — scripted providers for testing without audio I/O.

### Transport

- `twilio_ws` — Twilio Media Streams WebSocket handler. Registers `/voice` endpoint on FastAPI. Five concurrent tasks: `send_audio`, `send_marks`, `watch_disconnect`, `send_clears`, `stt`.
- `twilio_call` — outbound call initiation and TwiML generation via Twilio REST.
- `twilio_sms` — SMS delivery via Twilio REST.
- `MockBridge` — in-process bridge for FSM testing without a live WebSocket.

### Audio

- `AudioMixer` — real-time two-channel μ-law mixer with optional WAV recording. Requires `ffmpeg`/`ffplay`.
- `mix_frames` — mix two 160-byte μ-law frames (decode → add → clamp → re-encode).
- Inline G.711 μ-law codec — no `audioop` dependency (removed in Python 3.13).

### Design decisions

- **Persistent STT** — one STT session per call lifetime, not per-utterance. Avoids connection overhead and preserves acoustic context.
- **Protocol-based providers** — `SttProvider` and `TtsProvider` are structural types. Providers are swappable without inheritance.
- **sync→async bridging** — all sync→async calls use `asyncio.run_coroutine_threadsafe()`. Consumers run in sync threads; transport owns the event loop.
- **Transport intent** — consumers call `request_disconnect()` / `request_clear_buffer()`; the transport acts in protocol-specific terms.
