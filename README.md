# voice_runtime

Provider-agnostic voice call runtime for telephony projects. Manages audio queues, mark synchronization, STT/TTS providers, and transport protocols — so consumers focus on conversation logic, not plumbing.

## Quick Example

Make a call, say something, listen for a response via `on_committed` callback, hang up:

```python
import asyncio
import threading
import time
import uvicorn
from fastapi import FastAPI

from voice_runtime.session import VoiceSession
from voice_runtime.transports.twilio_ws import register_voice_websocket
from voice_runtime.transports.twilio_call import initiate_outbound_call
from voice_runtime.tts import create_tts
from voice_runtime.stt import create_stt

# 1. Create session and start WebSocket server
session = VoiceSession()
app = FastAPI()
register_voice_websocket(app, session)

def run_server():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    session.set_loop(loop)
    loop.run_until_complete(uvicorn.Server(
        uvicorn.Config(app, host="0.0.0.0", port=8080, log_level="warning")
    ).serve())

threading.Thread(target=run_server, daemon=True).start()
time.sleep(1)  # wait for server to start

# 2. Initiate call (Twilio calls back to our /voice WebSocket)
call_sid = initiate_outbound_call("+358401234567")
session.call_sid = call_sid
session.wait_for_ws_connect(timeout=30)

# 3. Speak
tts = create_tts()  # default: ElevenLabs
tts.speak("Hello! How are you today?", session)
session.send_mark_and_wait("after-greeting")  # block until playback done

# 4. Listen — persistent STT with on_committed callback
transcript_event = threading.Event()
heard = []

def on_committed(text: str):
    heard.append(text)
    transcript_event.set()

session.stt_factory = lambda: create_stt()  # default: ElevenLabs
# Transport starts STT automatically; on_committed fires for each utterance

transcript_event.wait(timeout=30)
print(f"Caller said: {heard}")

# 5. Hang up
session.request_disconnect()
```

### What happens under the hood

```
Consumer thread              voice_runtime                 Transport (Twilio)
─────────────────────────────────────────────────────────────────────────────
initiate_outbound_call()  →  Twilio REST calls.create()  → Twilio dials phone
                             build_stream_twiml()           with <Connect><Stream>
wait_for_ws_connect()     ←  signal_ws_connected()       ← Twilio opens /voice WS
                                                            stt_factory() → stt.start()
tts.speak(text, session)  →  ffmpeg MP3→μ-law             → send_audio task
                             put_outbound_sync()             sends base64 frames
send_mark_and_wait()      →  get_pending_mark()           → WS sends mark JSON
                          ←  signal_mark_received()       ← WS receives mark echo
on_committed(text)        ←  stt.on_committed()           ← WS receives media frames
                                                             decodes base64 → STT
request_disconnect()      →  _disconnect_requested.set()  → watch_disconnect task
                                                             stt.stop(), closes WS
                                                             Twilio ends call
```

## Architecture

```
┌──────────────────────────────────────────────┐
│ Consumer (outcaller, ninchat_voice)          │
│  - Subclass VoiceSession (e.g. TelcoSession) │
│  - Call speak(), listen(), hang up           │
├──────────────────────────────────────────────┤
│ voice_runtime                                │
│  - VoiceSession: queues, marks, intents      │
│  - Factories: create_stt / create_tts        │
│  - Providers: ElevenLabs, Azure (STT + TTS)  │
│  - SttTee: dual-provider fan-out             │
│  - Audio: G.711 μ-law codec + mixer          │
├──────────────────────────────────────────────┤
│ Transport (protocol-specific)                │
│  - twilio_ws: Media Streams WebSocket        │
│  - twilio_call: REST call initiation + TwiML │
└──────────────────────────────────────────────┘
```

**Key invariant:** VoiceSession has zero transport or provider imports. Consumers never import Twilio or ElevenLabs directly — they use factories and the intent API.

## Factories

Provider-agnostic factories mirror the yamlgraph `create_llm()` pattern:

```python
from voice_runtime.stt import create_stt, get_stt_class
from voice_runtime.tts import create_tts
from voice_runtime.transport import create_transport

stt = create_stt(provider="elevenlabs")     # or "azure"
tts = create_tts(provider="elevenlabs")     # or "azure"
transport = create_transport(provider="twilio")

# get_stt_class returns the class without instantiating (for factory arguments)
SttClass = get_stt_class(provider="elevenlabs")
session.stt_factory = lambda: SttClass(language_code="en")
```

## SttProvider Protocol

All STT providers implement this structural protocol (defined in `providers/__init__.py`):

```python
class SttProvider(Protocol):
    on_committed: Callable[[str], None] | None   # callback for committed transcripts

    def set_speaking(self, speaking: bool) -> None: ...
    async def start(self, inbound_queue: asyncio.Queue[bytes | None]) -> None: ...
    async def stop(self) -> None: ...
```

The `on_committed` callback is the primary mechanism for receiving transcriptions. Transport starts/stops the provider; the provider fires `on_committed` for every committed utterance past the echo discard window. The consumer decides routing (queue, dispatch, ignore).

## VoiceSession

Central coordinator between sync tool threads, async transport, and STT/TTS providers.

### Audio I/O

| Method | Thread safety | Purpose |
|--------|---------------|---------|
| `put_inbound(data)` | Any → async | Enqueue caller audio (transport calls this) |
| `get_outbound()` | async only | Dequeue agent audio (transport reads this) |
| `put_outbound_sync(data)` | Sync → async | Enqueue agent audio (TTS provider calls this) |
| `clear_inbound()` | Any | Drain stale audio frames |

All sync→async bridging uses `asyncio.run_coroutine_threadsafe()`.

### Mark Synchronization

Marks let sync tool code block until the transport confirms audio playback reached a point. This is how you know a TTS utterance finished playing before you start listening.

```python
tts.speak("What is your name?", session)
session.send_mark_and_wait("after-question", timeout=10.0)
# Now safe to start listening — caller heard the full question

session.clear_inbound()
transcript = stt.listen(session, timeout=30)
```

| Method | Purpose |
|--------|---------|
| `send_mark_and_wait(name, timeout)` | Block sync thread until mark echoed |
| `signal_mark_received(name)` | Called by transport when mark arrives |
| `get_pending_mark()` | Async — transport reads next mark to send |

### Transport Intent (NC-154)

Consumers signal *what* they want; the transport decides *how*.

```python
session.request_disconnect()     # transport closes connection, call ends
session.request_clear_buffer()   # transport discards buffered audio (barge-in)
```

Both are thread-safe. The transport watches `_disconnect_requested` (asyncio.Event) and `_clear_queue` (asyncio.Queue) and acts in its own protocol's terms — e.g. Twilio closes the WebSocket, which ends the call; SIP would send BYE.

### STT Factory

Attach an STT factory and the transport manages its lifecycle automatically:

```python
from voice_runtime.stt import create_stt

session.stt_factory = lambda: create_stt(provider="elevenlabs")
# Transport calls stt_factory() on stream start, stt.stop() on disconnect
```

Optional secondary STT for parallel logging/comparison (via `SttTee`):

```python
session.stt_secondary_factory = lambda: create_stt(provider="azure")
# Transport wraps both in SttTee — primary drives on_committed, secondary logs only
```

### Lifecycle

| Method | Purpose |
|--------|---------|
| `signal_ws_connected(stream_sid)` | Transport calls when connection established |
| `wait_for_ws_connect(timeout)` | Consumer blocks until connected; raises `CallNotAnsweredError` |
| `signal_disconnected()` | Transport calls on hangup |
| `is_disconnected` | Property — check if call ended |
| `reset()` | Clear all state for session reuse (multi-call servers) |

### Audio Monitoring

Optional two-channel mixer for real-time call monitoring (requires `ffplay`):

```python
from voice_runtime.audio import AudioMixer

mixer = AudioMixer()
mixer.start()
session.set_mixer(mixer)
# session.tap_caller() / session.tap_agent() now feed audio to ffplay
```

### Exceptions

| Exception | When |
|-----------|------|
| `MissingStreamUrlError` | `VOICE_STREAM_URL` env var not set |
| `CallNotAnsweredError(timeout)` | WebSocket didn't connect within timeout |
| `CallHangupError` | Call hung up during a listen operation |

## Transport: Twilio

### WebSocket Handler

Registers a `/voice` endpoint on a FastAPI app implementing Twilio Media Streams:

```python
from voice_runtime.transports.twilio_ws import register_voice_websocket

app = FastAPI()
register_voice_websocket(app, session)
```

Runs 5 async tasks on stream start: `send_audio`, `send_marks`, `watch_disconnect`, `send_clears`, `stt` (if factory provided).

### Call Initiation

```python
from voice_runtime.transports.twilio_call import (
    initiate_outbound_call,
    build_stream_twiml,  # alias: build_stream_xml
)

# Outbound: dial phone, Twilio connects back to /voice WebSocket
call_sid = initiate_outbound_call("+358401234567")

# Inbound webhook: return TwiML that tells Twilio to stream audio to /voice
xml = build_stream_twiml("wss://example.ngrok.io")
```

## Providers

### ElevenLabs TTS

Streams text → ElevenLabs API → ffmpeg (MP3 → μ-law 8kHz) → session outbound queue.

```python
from voice_runtime.tts import create_tts

tts = create_tts(provider="elevenlabs")
result = tts.speak("Hello", session, stop_event=barge_in_event)
# result: {"last_spoken": "Hello"} or {"last_spoken": "Hello", "call_disconnected": True}
```

Supports barge-in interrupt: pass a `threading.Event` as `stop_event`; set it from another thread to cut TTS mid-stream. Result may include `{"interrupted": True}`.

### ElevenLabs STT

Persistent Scribe WebSocket per call lifetime. Barge-in detection, echo discard, reconnect on fatal errors.

```python
from voice_runtime.stt import create_stt

stt = create_stt(provider="elevenlabs")
stt.on_committed = lambda text: print(f"Heard: {text}")
await stt.start(session.inbound)
# ... later
await stt.stop()
```

Typically managed by the transport via `session.stt_factory` rather than started manually.

### Azure TTS

Streams text → Azure Speech SDK → mulaw 8kHz → session outbound queue.

```python
from voice_runtime.tts import create_tts

tts = create_tts(provider="azure")
result = tts.speak("Hello", session, stop_event=barge_in_event)
```

Same `speak()` interface as ElevenLabs. Uses `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION`, `AZURE_TTS_VOICE` env vars.

### Azure STT

Persistent Azure Speech SDK recognizer with continuous recognition.

```python
from voice_runtime.stt import create_stt

stt = create_stt(provider="azure", language_code="fi-FI", silence_timeout_ms=1500)
stt.on_committed = lambda text: print(f"Heard: {text}")
await stt.start(session.inbound)
```

Same `SttProvider` protocol as ElevenLabs. Echo discard window (400ms) after TTS ends.

### SttTee (Dual STT)

Fan-out adapter running two STT providers on the same audio stream:

```python
from voice_runtime.stt_tee import SttTee

tee = SttTee(primary=elevenlabs_stt, secondary=azure_stt)
tee.on_committed = handler  # proxied to primary only
await tee.start(session.inbound)
```

Primary drives production (`on_committed`). Secondary receives same frames for logging/comparison only — its errors never propagate. Typically wired automatically via `session.stt_secondary_factory`.

## Audio Codec

G.711 μ-law at 8kHz — Twilio's native format. 160 bytes = 20ms frame.

```python
from voice_runtime.audio import mix_frames

mixed = mix_frames(caller_chunk, agent_chunk)  # mix two 160-byte frames
```

## Environment Variables

| Variable | Purpose | Required |
|----------|---------|----------|
| `VOICE_STREAM_URL` | Public WebSocket URL for transport callback | Yes |
| `VOICE_SERVER_PORT` | Uvicorn listen port | No (default: 8080) |
| `TWILIO_ACCOUNT_SID` | Twilio credentials (call initiation) | For outbound |
| `TWILIO_AUTH_TOKEN` | Twilio credentials | For outbound |
| `TWILIO_PHONE_NUMBER` | Outbound caller ID | For outbound |
| `ELEVENLABS_API_KEY` | ElevenLabs authentication | For ElevenLabs |
| `ELEVENLABS_VOICE_ID` | TTS voice | No (default: Rachel) |
| `ELEVENLABS_MODEL` | TTS model | No (default: `eleven_multilingual_v2`) |
| `STT_MODEL_ID` | ElevenLabs STT model | No (default: `scribe_v2_realtime`) |
| `STT_LANGUAGE_CODE` | ElevenLabs STT language | No (default: `fi`) |
| `AZURE_SPEECH_KEY` | Azure Speech SDK authentication | For Azure |
| `AZURE_SPEECH_REGION` | Azure Speech SDK region | No (default: `westeurope`) |
| `AZURE_TTS_VOICE` | Azure TTS voice name | No (default: `fi-FI-NooraNeural`) |
| `VOICE_MONITOR` | Enable AudioMixer monitoring | No (default: off) |

## Consumer Pattern

Typical consumer subclasses `VoiceSession` and adds server lifecycle:

```python
from dataclasses import dataclass
from voice_runtime.session import VoiceSession

@dataclass
class TelcoSession(VoiceSession):
    def start(self):
        app = FastAPI()
        register_voice_websocket(app, self)
        # Run uvicorn in daemon thread
        threading.Thread(target=self._run_loop, daemon=True).start()

    def shutdown(self):
        # Signal event loop to stop, join thread
        ...
```

Tool nodes then use the session for audio I/O, mark sync, and transport intents — without knowing anything about Twilio, WebSockets, or ElevenLabs.

### Known Consumers

- **[ninchat_voice](../ninchat_voice/)** — FSM-based Ninchat chatbot voice coordinator. Uses `TelcoSession` in `services/telephony.py` as a consumer wrapper. Multi-call session reuse with per-call reset.
- **[outcaller](../outcaller/)** — YAMLGraph-orchestrated outbound/inbound voice caller. Uses `TelcoSession` in `nodes/coordinator.py`.

## Multi-Call Session Reuse

When servers handle multiple sequential calls on the same `VoiceSession` instance, `reset()` clears all state between calls:

- Stops active STT via `asyncio.run_coroutine_threadsafe(stt.stop(), loop)` before clearing the reference (prevents orphaned WebSocket connections)
- Drains inbound and outbound queues
- Resets mark synchronization and transport intent events

The STT `start()` method also drains the inbound queue as defense-in-depth against sentinel values from prior call cleanup.

### STT Reconnect on Fatal Errors

`PersistentSttSession` detects fatal WebSocket errors (connection closed, protocol errors) and automatically reconnects:

- `_on_error()` schedules `_reconnect_after_error()` for errors in `_FATAL_ERRORS`
- Reconnect drains stale frames, creates a new WebSocket, and resumes the feed task
- `_feed_audio()` wraps `send()` in try/except for dead socket resilience
