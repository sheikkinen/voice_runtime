# Feature Request: Mock Transport Bridge for E2E Testing

**Priority:** HIGH
**Type:** Enhancement
**Status:** Approved
**Effort:** 3 days
**Requested:** 2026-05-05
**Supersedes:** NC-269, NC-270

## Summary

Add a mock transport mode to voice_runtime that bridges two voice processes over localhost WebSockets with text relay instead of audio, enabling the existing `test-marketing-e2e.sh` to run without Twilio/ngrok/ElevenLabs by setting `TTS_PROVIDER=mock STT_PROVIDER=mock TRANSPORT=mock`.

## Value Statement

The exact same E2E test — same `start-marketing.sh`, same `start-marketing-answerer.sh`, same assertions — runs in two modes: real (Twilio + ElevenLabs) or mock (localhost + text relay). No test duplication. No new test drivers. Sub-60s CI regression test with zero telephony cost.

## Problem

`test-marketing-e2e.sh` requires Twilio API, ngrok, and ElevenLabs. It cannot run in CI. NC-269 attempted to solve this by replacing the outcaller with scripted `inject()` calls — wrong boundary. The outcaller IS the test driver; replacing it duplicates conversation logic and doesn't test the outcaller's voice_runtime integration.

## Key Insight

The real call flow is:

```
outcaller MockTts.speak("text")        ninchat_voice MockStt ← needs "text"
         ↓                                      ↑
    session.outbound (empty)          session.inbound (empty)
         ↓                                      ↑
    twilio_ws /voice ──── [Twilio PSTN] ──── twilio_ws /voice
```

With mock providers, MockTts records text but produces no audio frames. MockStt waits for `inject()` but nobody calls it. The WebSocket path carries nothing useful. We need a **text relay** that captures MockTts output and delivers it to the other side's MockStt.

## Proposed Solution

### Architecture: Text Sideband

Instead of flowing text through the audio WebSocket, add a text sideband to MockTts and MockStt that connects them across processes.

```
┌─────────────────────┐                    ┌─────────────────────┐
│  Outcaller          │                    │  ninchat_voice      │
│                     │                    │                     │
│  MockTts.speak(txt) │──── HTTP POST ────►│  MockStt.inject(txt)│
│  ← on_committed(txt)│◄── HTTP POST ─────│  MockTts.speak(txt) │
│  MockStt            │                    │                     │
│                     │                    │                     │
│  /voice WS ─────────│─── dummy WS ──────│─ /voice WS          │
│  (mark echo only)   │  (signaling only)  │  (mark echo only)   │
└─────────────────────┘                    └─────────────────────┘
```

### Three Changes to voice_runtime

#### 1. MockTts: text broadcast callback

Add an optional `on_spoken` callback to MockTts. When set, `speak()` calls `on_spoken(text)` after recording. This is the hook for the text relay.

```python
class MockTts:
    def __init__(self, on_spoken=None, **kwargs):
        self.spoken: list[str] = []
        self.on_spoken = on_spoken  # NEW: callback for text relay
        # ...

    def speak(self, text, session, stop_event=None):
        self.spoken.append(text)
        if self.on_spoken:
            self.on_spoken(text)  # NEW: broadcast
        # Still need mark sync for FSM timing:
        session.send_mark_and_wait("tts_complete", timeout=10.0)
        return {"last_spoken": text, "interrupted": ...}
```

#### 2. MockStt: HTTP inject endpoint

Add an optional `/test/inject` endpoint capability. When `TRANSPORT=mock`, the voice server exposes `POST /test/inject` that calls `session.stt.inject(text)`.

```python
# In the mock transport setup (not in MockStt itself):
@app.post("/test/inject")
async def inject_stt(request: Request):
    body = await request.json()
    session = get_active_session()
    session.stt.inject(body["text"])
    return {"ok": True}
```

#### 3. Mock transport: replace Twilio call initiation

Add `initiate_mock_call()` to `voice_runtime/transports/`:

```python
def initiate_mock_call(target_url: str) -> str:
    """Replace Twilio API call with direct HTTP + WS to target server."""
    # 1. POST /incoming to target (replaces Twilio webhook)
    resp = httpx.post(f"{target_url}/incoming", data={
        "CallSid": f"CAMOCK_{uuid4().hex[:8]}",
        "From": "+358400000000",
    })
    # 2. Connect FakeWS to target's /voice (replaces Twilio Media Streams)
    #    Send connected + start events, echo marks
    ws_url = target_url.replace("http://", "ws://") + "/voice"
    _start_fake_ws_bridge(ws_url)
    # 3. Return fake call SID
    return f"CAMOCK_{uuid4().hex[:8]}"
```

### Text Relay Wiring

The relay is configured at startup via env vars:

**On outcaller side** (`TTS_PROVIDER=mock STT_PROVIDER=mock TRANSPORT=mock`):
- MockTts gets `on_spoken` callback that POSTs to `{MOCK_TARGET_URL}/test/inject`
- Server exposes `POST /test/inject` for receiving text from ninchat_voice's MockTts
- `initiate_mock_call(MOCK_TARGET_URL)` replaces `initiate_outbound_call(phone)`

**On ninchat_voice side** (`TTS_PROVIDER=mock STT_PROVIDER=mock TRANSPORT=mock`):
- MockTts gets `on_spoken` callback that POSTs to `{MOCK_TARGET_URL}/test/inject`
- Server exposes `POST /test/inject` for receiving text from outcaller's MockTts
- No call initiation needed (receives the mock call)

### FakeWsBridge

Minimal WebSocket client that satisfies the Twilio Media Streams protocol:

```python
class FakeWsBridge:
    """Connects to a /voice endpoint, performs Twilio handshake, echoes marks."""

    async def connect(self, ws_url: str) -> None:
        self._ws = await websockets.connect(ws_url)
        await self._ws.send(json.dumps({"event": "connected"}))
        await self._ws.send(json.dumps({
            "event": "start",
            "streamSid": f"MZ{uuid4().hex[:8]}",
            "start": {"callSid": self._call_sid},
        }))

    async def run(self) -> None:
        """Echo marks, ignore media, until stop."""
        async for msg in self._ws:
            data = json.loads(msg)
            if data.get("event") == "mark":
                await self._ws.send(json.dumps({
                    "event": "mark",
                    "mark": {"name": data["mark"]["name"]},
                }))
```

### Mark Synchronization (Critical)

MockTts.speak() currently returns immediately. The real TTS calls `session.send_mark_and_wait("tts_complete")` which blocks until Twilio echoes the mark. This timing is critical for FSM state transitions (speak → speak_done → listen).

MockTts **must** also call `send_mark_and_wait()` so the bridge server sends `speak_done` to the FSM engine at the right time. The FakeWsBridge echoes marks back instantly, so the block is near-zero but the protocol is preserved.

### Modified Test Script

```bash
#!/bin/bash
# test-marketing-e2e-mock.sh
# Same flow as test-marketing-e2e.sh but without Twilio/ngrok/ElevenLabs.

export TTS_PROVIDER=mock
export STT_PROVIDER=mock
export TRANSPORT=mock
export MOCK_TARGET_URL="http://127.0.0.1:${OUTCALLER_PORT:-8080}"

# On outcaller side:
export MOCK_PEER_URL="http://127.0.0.1:${NV_PORT:-8000}"

# Same start-marketing.sh (skips ngrok when TRANSPORT=mock)
# Same start-marketing-answerer.sh (skips ngrok, uses mock transport)
# Same assertions on coordinator log
```

Or better: the existing `test-marketing-e2e.sh` accepts a `--mock` flag:

```bash
./test-marketing-e2e.sh          # real Twilio + ElevenLabs
./test-marketing-e2e.sh --mock   # mock transport + mock providers
```

### What's Real vs Mocked

| Component | Real mode | Mock mode |
|---|---|---|
| FSM engine subprocess | REAL | REAL |
| Coordinator YAML config | REAL | REAL |
| FSM actions | REAL | REAL |
| Bridge server (uvicorn) | REAL | REAL |
| BridgeListener + handlers | REAL | REAL |
| YAMLGraph pipeline | REAL | REAL |
| Outcaller graph | REAL | REAL |
| Outcaller uvicorn server | REAL | REAL |
| TTS provider | ElevenLabs | MockTts (text relay) |
| STT provider | ElevenLabs | MockStt (text relay) |
| Call transport | Twilio PSTN | localhost HTTP+WS |
| ngrok tunnels | 2 tunnels | 0 (localhost) |
| LLM (Gemini Flash) | REAL | REAL |

### What Needs Building (in voice_runtime)

1. **MockTts.on_spoken callback** — ~5 lines added to `mock/tts.py` + `send_mark_and_wait()` call
2. **FakeWsBridge** — ~40 lines in `transports/mock_bridge.py`
3. **`initiate_mock_call()`** — ~20 lines in `transports/mock_bridge.py`
4. **`/test/inject` route setup** — conditional route in transport setup when `TRANSPORT=mock`
5. **Transport factory** — `TRANSPORT` env var selects real (Twilio) vs mock transport

### What Needs Building (in ninchat_voice)

6. **`/test/inject` route** in `server_fsm.py` — gated on `TRANSPORT=mock`
7. **MockTts on_spoken wiring** — connect callback to POST peer's `/test/inject`
8. **Skip ngrok** in `start-marketing.sh` when `TRANSPORT=mock`

### What Needs Building (in outcaller)

9. **Mock call initiation** in `nodes/twilio_call.py` — when `TRANSPORT=mock`, call `initiate_mock_call()` instead of `initiate_outbound_call()`
10. **`/test/inject` route** in `server.py` — same pattern as ninchat_voice
11. **MockTts on_spoken wiring** — connect callback to POST peer's `/test/inject`
12. **Skip ngrok** in `start-marketing-answerer.sh` when `TRANSPORT=mock`

### What Needs Building (in test)

13. **`test-marketing-e2e.sh --mock` flag** — sets env vars, skips Twilio/ngrok requirements

## Acceptance Criteria

- [ ] `./test-marketing-e2e.sh --mock` runs the full marketing E2E without Twilio/ngrok/ElevenLabs
- [ ] Same `start-marketing.sh` used in both modes (skips ngrok when `TRANSPORT=mock`)
- [ ] Same `start-marketing-answerer.sh` used in both modes
- [ ] FSM engine runs as real subprocess, unaware of mock mode
- [ ] Coordinator log assertions identical to real mode
- [ ] MockTts text is relayed to peer's MockStt via HTTP POST
- [ ] Mark synchronization preserved (MockTts calls `send_mark_and_wait`)
- [ ] FakeWsBridge echoes marks for protocol compliance
- [ ] No new test drivers or duplicate conversation logic
- [ ] Requires only `GOOGLE_API_KEY` (for LLM pipeline) in mock mode
- [ ] Wall time < 60s in mock mode
- [ ] `./test-marketing-e2e.sh` (no flag) continues to work unchanged with real Twilio

## Security Notes

- `/test/inject` endpoint must be **fail-closed**: the route handler must explicitly check `os.environ.get("TRANSPORT") == "mock"` and return 404 if not set, even if the route was conditionally registered. Defense in depth.
- Not exposed in production (TRANSPORT defaults to real/unset)
- No authentication needed — localhost only, test-only path

## Alternatives Considered

1. **NC-269: Replace outcaller with scripted inject()** — Rejected. Duplicates conversation logic, doesn't test outcaller integration, creates divergent test paths.
2. **Mock audio frames with embedded text** — Overengineers the WS path. Text sideband is simpler.
3. **Shared-memory IPC between processes** — Complex, couples processes. HTTP POST is standard and debuggable.
4. **Don't mock TTS/STT, only mock transport** — Still requires ElevenLabs API key, defeats "no API keys" goal.

## Out of Scope

(Inherited from NC-270 judgement conditions)

- Replacing the real `test-marketing-e2e.sh`; it remains the final Twilio/provider integration gate.
- Validating real STT accuracy or TTS audio quality.
- Simulating packet loss, jitter, PSTN latency, or Twilio provider failures.
- Rewriting the marketing answerer graph.
- Creating a separate mock-only answerer script.

## Judgement Conditions (2026-05-05)

1. **Inherit NC-270's Out of Scope** — done (above).
2. **`/test/inject` must be fail-closed** — route handler must check `TRANSPORT == "mock"` and 404 otherwise, even if conditionally registered. See Security Notes.
3. **MockTts `send_mark_and_wait` is a prerequisite change** — must be added to `voice_runtime/mock/tts.py` before the relay layer. FSM timing depends on it.
4. **`on_spoken` callback must default to `None`** — MockTts works standalone for unit tests that don't need relay. The callback is relay-specific, not provider-contract.

## Related

- NC-267: Mock TTS/STT providers (prerequisite — done)
- NC-268: Outcaller provider factory refactor (prerequisite — done)
- NC-269: Mock marketing E2E (superseded by this FR)
- NC-270: Voice runtime mock route (superseded by this FR; constraints inherited)
