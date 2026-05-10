# Changelog

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
