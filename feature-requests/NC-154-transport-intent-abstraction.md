# NC-154: Transport Intent Abstraction in voice_runtime

**Status:** Enforcing — outcaller GREEN, voice_runtime done, ninchat_voice pending
**Date:** 2026-03-19
**Judged:** 2026-03-19
**Depends on:** NC-153 (voice_runtime consumer adaptation) — DONE (telephony, persistent_stt, tts)

## Implementation Progress

### voice_runtime (DONE — uncommitted)

- `session.py`: Added `request_disconnect()`, `request_clear_buffer()`,
  `stt_factory`, `stt`, `_disconnect_requested`, `_clear_queue` fields.
  Lazy init in `signal_ws_connected()`. Reset in `reset()`. (+44 lines)
- `transports/twilio_ws.py`: Added `watch_disconnect()`, `send_clears()`,
  STT lifecycle (factory create on start, stop in finally). 5 async tasks
  total: send_audio, send_marks, disconnect, clear, stt. (+52 lines)
- `transports/twilio_call.py`: NEW — `build_stream_twiml()`,
  `build_stream_xml` alias, `initiate_outbound_call()`, `_get_twilio_env()`.
  Extracted from outcaller's `nodes/twilio_call.py`.
- `tests/test_twilio_ws.py`: Mock session gains NC-154 fields (set to None).
- `README.md`: NEW — full interface documentation.
- **79/79 tests pass.**

### outcaller (DONE — RED committed 585f3fb, GREEN uncommitted)

- RED commit `585f3fb`: `tests/unit/test_no_twilio_deps.py` — 3 gate tests
  scanning production code for Twilio imports, WebSocket imports, and
  wire protocol literals (`streamSid`, `TwiML`, `<Stream>`, `<Connect>`).
- `nodes/twilio_call.py`: Removed `from twilio.rest import Client`,
  removed `TWILIO_*` env vars, removed TwiML generation. `initiate_call()`
  delegates to `voice_runtime.transports.twilio_call.initiate_outbound_call()`.
  `end_call()` uses `session.request_disconnect()` instead of REST API.
- `server_incaller.py`: TwiML generation delegated to
  `voice_runtime.transports.twilio_call.build_stream_xml()`. Removed
  module-level `VOICE_STREAM_URL` constant (reads env at request time).
- `nodes/twilio_inbound.py`: Cleaned Twilio-specific docstrings.
- `tests/unit/test_telco_nodes.py`: TestInitiateCall mocks updated to
  patch `voice_runtime.transports.twilio_call.initiate_outbound_call`
  instead of removed `VOICE_STREAM_URL` and `twilio.rest.Client`.
- `tests/unit/test_incaller.py`: TestIncomingWebhook uses
  `patch.dict("os.environ")` instead of patching removed module attribute.
- **189/189 tests pass** (186 original + 3 gate).
- **Live demo confirmed:** outbound call to +358400000000 worked end-to-end.

### ninchat_voice (NOT STARTED)

Remaining work:
1. Write RED gate tests (`test_no_twilio_deps.py`) — scan production code
2. Delete `services/twilio_ws.py` (180 lines)
3. Rename `request_close_ws()` → `request_disconnect()` in `TelcoSession`
4. Rename `schedule_twilio_clear()` → `request_clear_buffer()` in `TelcoSession`
5. Remove `_close_ws_event`, `_clear_queue`, `signal_ws_connected()` override
   from `TelcoSession` (moved to VoiceSession base)
6. Wire `stt_factory` in `server_fsm.py` instead of direct STT creation
7. Update `call_abort_action.py` references
8. Delete 6 NC-130 source-inspection tests in `test_nc130_persistent_stt.py`
9. Verify 360+ tests pass

---

## Problem

NC-153 deferred `twilio_ws.py` and proposed Option A: an `on_stream_start`
callback to inject consumer tasks into voice_runtime's transport handler.
This is the "downstream fix" trap from the Scripture — it gives the consumer
transport knowledge it shouldn't have.

ninchat_voice's three "extra tasks" in `twilio_ws.py` are not consumer
concerns:

1. **`watch_close`** — server-initiated disconnect. Every transport needs
   this. Twilio closes the WebSocket. SIP sends BYE. The *session* decides
   to hang up; the *transport* executes it in its own protocol.

2. **`_send_clears`** — barge-in buffer discard. This is a Twilio wire
   protocol `clear` event. A SIP trunk discards buffers differently. The
   consumer should say "clear the buffer," not "send a Twilio clear JSON."

3. **`stt_task`** — STT lifecycle. voice_runtime already owns
   `PersistentSttSession` and the `inbound` queue. The transport knows
   when audio starts. STT wiring is session-level, not consumer-level.

The consumer says *what*. The transport says *how*. ninchat_voice's
`twilio_ws.py` (180 lines) should be eliminated, not delegated.

## Goal

Move transport intent into `VoiceSession` as abstract operations. Each
transport implementation responds to those intents in its own protocol.
After this, ninchat_voice deletes `services/twilio_ws.py` and imports
`register_voice_websocket` from voice_runtime — matching what outcaller
already does.

**Explicit target:** Neither outcaller nor ninchat_voice may have
dependencies on the Twilio API or audio WebSocket protocol. No consumer
imports `twilio`, `websocket`, or sends/receives Twilio wire messages.
All Twilio-specific code — including call initiation (`calls.create()`),
termination, and media streaming — lives exclusively in
`voice_runtime/transports/`. Twilio is one telephony provider; it will
be replaced. Consumers interact only with `VoiceSession` intent methods.

## Design

### 1. VoiceSession gains intent methods

```python
# voice_runtime/session.py

def request_disconnect(self) -> None:
    """Consumer requests call termination. Thread-safe.

    Transport watches _disconnect_requested and closes in its own way.
    """
    self._disconnect_requested.set()

def request_clear_buffer(self) -> None:
    """Consumer requests outbound buffer discard. Thread-safe.

    Transport watches _clear_requested queue and sends protocol-specific
    clear command (Twilio: 'clear' event, SIP: flush, etc.)
    """
    self._clear_queue.put_nowait(self.stream_sid)
```

These replace ninchat_voice's `request_close_ws()` and
`schedule_twilio_clear()` — same semantics, but transport-agnostic names
on the base class.

### 2. VoiceSession gains optional STT lifecycle

```python
# voice_runtime/session.py

stt: PersistentSttSession | None = None

def attach_stt(self, stt: PersistentSttSession) -> None:
    """Attach STT to this session. Transport starts it on audio flow."""
    self.stt = stt
```

Transport creates and starts the STT task when the stream begins, stops
and cancels it on disconnect. Consumer calls `attach_stt()` before the
call if it wants transcription; transport handles the lifecycle.

Alternatively, if the session should own STT creation entirely, a factory
callback pattern:

```python
stt_factory: Callable[[], PersistentSttSession] | None = None
```

Transport calls `session.stt = session.stt_factory()` on stream start if
a factory is provided.

### 3. Twilio transport handles intents

```python
# voice_runtime/transports/twilio_ws.py — gains 3 tasks

async def watch_disconnect() -> None:
    """Watch for session.request_disconnect() and close WebSocket."""
    await session._disconnect_requested.wait()
    await websocket.close(1000)

async def send_clears() -> None:
    """Watch for session.request_clear_buffer() and send Twilio clear."""
    while True:
        sid = await session._clear_queue.get()
        await websocket.send_json({"event": "clear", "streamSid": sid})

# STT lifecycle in start event handler:
if session.stt is not None:
    stt_task = asyncio.create_task(session.stt.start(session.inbound))
```

### 4. ninchat_voice migration

| Before | After |
|--------|-------|
| `from services.twilio_ws import register_voice_websocket` | `from voice_runtime.transports.twilio_ws import register_voice_websocket` |
| `session.request_close_ws()` | `session.request_disconnect()` |
| `session.schedule_twilio_clear()` | `session.request_clear_buffer()` |
| `twilio_ws.py` creates PersistentSttSession | `server_fsm.py` calls `session.attach_stt(stt)` before call |
| `services/twilio_ws.py` (180 lines) | Deleted |

### 5. TelcoSession slims further

`TelcoSession` currently keeps `_close_ws_event`, `_clear_queue`,
`request_close_ws()`, `schedule_twilio_clear()`. All four move to
`VoiceSession`. TelcoSession retains only:

- `stt` field (consumer wires `_on_direct_dispatch`)
- `signal_ws_connected()` override (adds clear_queue + close_event init → these move to base too)
- `reset_for_new_call()` (calls `super().reset()`)
- `start()` / `_run_loop()` / `shutdown()` (uvicorn lifecycle)
- Session registry

## Acceptance criteria

1. `VoiceSession` has `request_disconnect()` and `request_clear_buffer()` methods
2. voice_runtime's `register_voice_websocket` handles disconnect watch, clear events, and optional STT lifecycle
3. ninchat_voice's `services/twilio_ws.py` is deleted
4. ninchat_voice imports `register_voice_websocket` from voice_runtime (matching outcaller)
5. ninchat_voice's `TelcoSession` no longer has `_close_ws_event`, `_clear_queue`, `request_close_ws()`, `schedule_twilio_clear()`
6. All 360 ninchat_voice unit tests pass
7. All voice_runtime tests pass
8. outcaller's `start_call()` and `end_call()` delegate to voice_runtime (zero `from twilio` in consumer)
9. `grep -rn "import twilio\|from twilio"` returns zero hits in both consumers (production code, excluding tests)
10. Neither consumer imports `WebSocket`, `WebSocketDisconnect`, or sends Twilio wire protocol messages (`streamSid`, `"event": "media"`, `"event": "clear"`)
11. Call initiation lives in voice_runtime (outcaller's `nodes/twilio_call.py` delegates to voice_runtime for `calls.create()`, not `from twilio.rest import Client`)

### TDD gate: no-twilio-in-consumer tests

Both consumers MUST have a RED test that **fails if any production code
imports twilio or references Twilio wire protocol**. These tests are
written first (RED) and must pass after migration (GREEN).

```python
# tests/test_no_twilio_deps.py (in each consumer)

@pytest.mark.req("NV-154-GATE")  # or OC-154-GATE for outcaller
class TestNoTwilioDeps:
    """NC-154: consumer must not depend on Twilio API or wire protocol."""

    def test_no_twilio_import_in_production_code(self) -> None:
        """No production .py file may contain 'from twilio' or 'import twilio'."""
        ...  # scan all .py files under services/, actions/, server_fsm.py
             # (ninchat_voice) or nodes/, server*.py (outcaller)
             # assert zero matches

    def test_no_twilio_websocket_import_in_production_code(self) -> None:
        """No production .py file may import FastAPI WebSocket for Twilio transport."""
        ...  # scan production files for 'from fastapi import.*WebSocket'
             # in services/twilio_ws.py or equivalent audio transport files
             # (ninchat_session.py WebSocket for Ninchat chat is unrelated)

    def test_no_twilio_wire_protocol_literals(self) -> None:
        """No production .py file may contain Twilio wire protocol literals."""
        ...  # scan for 'streamSid', '"event": "media"', '"event": "clear"',
             # '"event": "mark"', '"event": "connected"', '"event": "start"'
```

These tests enforce the invariant permanently — any future code that
reintroduces a Twilio dependency breaks the build.

## Risks

- **STT factory vs attach:** Need to decide whether consumer pre-creates
  the STT instance or provides a factory. Pre-creation is simpler but means
  STT is allocated even if the call never connects (Twilio drops).
- **`_on_direct_dispatch` wiring:** Consumer still needs to set the
  dispatch callback on the STT instance. If using `attach_stt()`, the
  consumer wires the callback before attaching. If using a factory, the
  factory closure captures the callback.
- **Future transports:** SIP, WebRTC, or other transports should respond
  to the same intent methods. The Twilio transport is the only
  implementation today — YAGNI for now but the abstraction must not be
  Twilio-specific in naming or semantics.

## Effort

- voice_runtime: ~50 lines added (intent methods + transport tasks)
- ninchat_voice: ~180 lines deleted, ~10 lines changed (callsite renames)
- Tests: ~15 tests affected (mock target changes for close/clear), NC-130 source inspection tests updated or removed

---

## Judgment — 2026-03-19

### Verdict: Conditionally Approved

The direction is correct. Transport intent belongs in VoiceSession.
ninchat_voice should not know what a Twilio `clear` JSON looks like.
The core abstraction — consumer says what, transport says how — is sound.

### Corrective actions required

**1. Unified termination model — both consumers converge on `request_disconnect()`.**

outcaller terminates calls via Twilio REST API (`client.calls(sid).update(status="completed")`)
and ninchat_voice terminates by closing the WebSocket. Both are design smells:
outcaller reaches *around* the transport to talk to Twilio directly, ninchat_voice
reaches *into* the transport to close the WebSocket. Neither goes *through*.

The clean model: `session.request_disconnect()` expresses intent. The Twilio WS
transport hears it and closes the server-side WebSocket. When Twilio's `<Stream>`
loses its connection, the call has no more instructions and Twilio ends it. This
works for both inbound and outbound calls — the REST API
`calls.update(status="completed")` is redundant when you own the media stream.

Twilio REST credentials (account SID, auth token) are only needed for *initiating*
outbound calls (`calls.create()`), not for terminating. Termination is the
transport's job.

**Action:** `request_disconnect()` is universal — both consumers use it.
outcaller's `end_call()` calls `session.request_disconnect()` instead of
the REST API. outcaller's `start_call()` delegates to voice_runtime for
call initiation — `from twilio.rest import Client` moves out of the
consumer entirely. Twilio is a replaceable provider, not a consumer dep.

**2. STT lifecycle: use `stt_factory`, not `attach_stt()`.**

The FR acknowledges the risk: "`attach_stt()` allocates before call connects."
But the deeper problem is **callback wiring timing**. The sequence today is:

1. Twilio `start` event fires → `twilio_ws.py` creates `PersistentSttSession()`
2. `server_fsm.py` sets `stt._on_direct_dispatch = lambda ...`
3. `stt.start(session.inbound)` begins consuming audio

With `attach_stt()`, step 1 moves to before the call. But step 2 (callback
wiring) stays in `server_fsm.py`. If the transport starts STT before the
consumer wires the dispatch callback, transcripts are silently dropped.

`stt_factory` solves this: the factory closure captures the dispatch callback.
The transport calls the factory on `start` event, getting a fully-wired instance.

```python
session.stt_factory = lambda: _make_wired_stt(sender)
```

Where `_make_wired_stt` creates the PersistentSttSession and wires
`_on_direct_dispatch` in one call. No split-phase wiring. No race.

**Action:** Use `stt_factory: Callable[[], PersistentSttSession] | None = None`.
Remove the `attach_stt()` alternative from the design. Transport calls factory
on stream start if set.

**3. `_clear_queue` threading model must match VoiceSession's existing pattern.**

VoiceSession uses `asyncio.run_coroutine_threadsafe()` for all sync→async
bridging (see `put_inbound()`, `put_outbound_sync()`, `schedule_twilio_clear()`).
The FR's design shows `self._clear_queue.put_nowait(self.stream_sid)` — this
is a direct `asyncio.Queue.put_nowait()` from a sync thread, which is not
thread-safe. It must use the same `run_coroutine_threadsafe` pattern.

**Action:** `request_clear_buffer()` must use
`asyncio.run_coroutine_threadsafe(self._clear_queue.put(...), self._loop)`,
matching TelcoSession's existing `schedule_twilio_clear()` implementation exactly.

**4. `signal_ws_connected()` override must survive.**

The FR says TelcoSession's `signal_ws_connected()` override "moves to base."
Today, TelcoSession's override lazily creates `_clear_queue` and `_close_ws_event`
in the uvicorn event loop context. If these fields move to VoiceSession, they
must be created in the same lazy pattern (on connect, not on construction) because
the event loop doesn't exist at construction time.

VoiceSession's existing `signal_ws_connected()` sets `stream_sid` and
`_ws_connected`. The `_clear_queue` and `_disconnect_requested` event should
be initialized there too, or in `set_loop()`.

**Action:** Initialize `_disconnect_requested` (asyncio.Event) and `_clear_queue`
(asyncio.Queue) in `signal_ws_connected()`, matching the lazy-init pattern.
TelcoSession no longer needs to override `signal_ws_connected()`.

**5. NC-130 source-inspection tests become invalid.**

Six tests in `test_nc130_persistent_stt.py` (lines 527-588) inspect
`services/twilio_ws.py` source code for `stt_task`, `clear_task` declarations.
When `twilio_ws.py` is deleted, these tests will fail with `FileNotFoundError`.
They must be either deleted (the behavior they guard is now in voice_runtime)
or retargeted to voice_runtime's `twilio_ws.py`.

**Action:** Delete the 6 source-inspection tests. Replace with behavioral tests
that verify voice_runtime's transport starts/stops STT and sends clears. Source
inspection was a stop-gap for NC-130; NC-154 replaces it with proper delegation.

**6. `call_abort_action.py` references `request_close_ws()` — must be updated.**

`actions/real/call_abort_action.py` documents that `reset_for_new_call()` sets
`_close_ws_event=None`, making `request_close_ws()` a no-op. After NC-154,
the comment and any code referencing `request_close_ws` must update to
`request_disconnect`.

**Action:** Update `call_abort_action.py` comments and any code paths.
Also update `server_fsm.py` line 410: `session.request_close_ws()` →
`session.request_disconnect()`.

### Scope freeze

After corrective actions:
- voice_runtime gains: `request_disconnect()`, `request_clear_buffer()`,
  `stt_factory` field, lazy init in `signal_ws_connected()`, 3 new tasks
  in Twilio transport
- ninchat_voice loses: `services/twilio_ws.py` (deleted), `_close_ws_event`,
  `_clear_queue`, `request_close_ws()`, `schedule_twilio_clear()`,
  `signal_ws_connected()` override, 6 source-inspection tests
- ninchat_voice gains: `stt_factory` wiring in `server_fsm.py`
- outcaller: `end_call()` uses `session.request_disconnect()`,
  `start_call()` delegates to voice_runtime for initiation.
  Zero `from twilio` imports remain in consumer code.

Authority granted to proceed with Enforce phase.
