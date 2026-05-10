# NC-267: Mock TTS/STT Providers — Scripted Testing at Provider Boundary

**Priority:** HIGH
**Type:** Feature
**Status:** Approved
**Effort:** 1–2 days
**Requested:** 2026-05-05
**Supersedes:** NC-266
**Prerequisites:** NC-268 (outcaller factory refactor)

## Summary

Add `MockTts` and `MockStt` providers to voice_runtime that conform to the
existing `TtsProvider` and `SttProvider` protocols. Both consumers select mock
providers via the existing factory (`create_tts(provider="mock")`), or
automatically via `VOICE_PROVIDER=mock` env var. No session subclassing,
no bridge patching, no consumer code changes.

## Problem

Same as NC-266. See that FR for the full test layer gap analysis.

**Key difference from NC-266:** NC-266 proposes a `MockVoiceSession` subclass
and patching `bridge_handlers.tts_speak` / `stt_listen`. This works for
ninchat_voice but doesn't match outcaller's architecture (single-process,
no bridge, tool nodes call providers directly via `get_active_session()`).

NC-267 targets the **provider boundary** — a layer both consumers share.

## Why Provider Boundary, Not Session

### Architecture comparison

```
ninchat_voice (two-process):
  bridge_handlers.tts_speak(text, session)
    → services/tts.py speak()
      → create_tts("elevenlabs").speak(text, session)    ← PROVIDER BOUNDARY

outcaller (single-process):
  nodes/tts.py speak(state)
    → ElevenLabsTTS().speak(text, session)               ← PROVIDER BOUNDARY
  nodes/stt.py listen_and_transcribe(state)
    → PersistentSttSession().start(inbound)              ← PROVIDER BOUNDARY
```

Both consumers converge at the same contracts:
- `TtsProvider.speak(text, session, stop_event) → dict`
- `SttProvider.start(inbound_queue)`, `SttProvider.on_committed` callback

### Existing factory support

voice_runtime already has factories that both consumers use:

```python
# voice_runtime/tts.py
def create_tts(provider: str = "elevenlabs", **kwargs) -> TtsProvider:
    # Supports: "elevenlabs", "azure"

# voice_runtime/stt.py
def create_stt(provider: str = "elevenlabs", **kwargs) -> SttProvider:
    # Supports: "elevenlabs", "azure"
```

Adding `provider="mock"` is a one-line change per factory.

### What NC-266 gets wrong

NC-266's `MockVoiceSession` subclasses `VoiceSession` and exposes `mock_speak()`
/ `mock_listen()` methods. But:

1. **Outcaller doesn't use bridge_handlers.** Tool nodes call providers directly.
   Patching `bridge_handlers.tts_speak` is ninchat_voice-specific.
2. **ninchat_voice's test_full_call_flow.py patches with plain lists**, not
   session methods. No mock session reference needed.
3. **Actions never hold session references.** ninchat_voice actions use DGRAM;
   outcaller tool nodes use `get_active_session()`. Neither benefits from a
   session subclass.

Mock providers work for both consumers without any consumer code changes.

## Proposed Solution

### MockTts

```python
# voice_runtime/mock/tts.py
class MockTts:
    """TtsProvider that captures text instead of generating audio.

    Conforms to TtsProvider protocol. No API keys, no audio, no network.
    """

    on_error: Callable[[str], None] | None = None

    def __init__(self) -> None:
        self.spoken: list[str] = []

    def speak(
        self,
        text: str,
        session: VoiceSession,
        stop_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        self.spoken.append(text)
        return {"last_spoken": text, "interrupted": False}
```

~20 lines. No `session.put_outbound_sync()`, no `session.send_mark_and_wait()`,
no audio. Just captures text.

### MockStt

```python
# voice_runtime/mock/stt.py
class MockStt:
    """SttProvider that fires scripted transcripts instead of recognizing audio.

    Conforms to SttProvider protocol. No API keys, no audio, no network.
    """

    on_committed: Callable[[str], None] | None = None
    on_recognizing: Callable[[str], None] | None = None
    on_error: Callable[[str], None] | None = None

    def __init__(self) -> None:
        self._utterances: asyncio.Queue[str] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False

    def inject(self, text: str) -> None:
        """Queue a transcript for the next on_committed callback.

        Thread-safe: can be called from test thread while start() runs
        in session's event loop.
        """
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._utterances.put_nowait, text)
        else:
            self._utterances.put_nowait(text)

    def set_speaking(self, speaking: bool) -> None:
        pass  # No echo discard needed

    async def start(self, inbound_queue: asyncio.Queue[bytes | None]) -> None:
        """Ignore audio. Fire on_committed from injected queue."""
        self._loop = asyncio.get_running_loop()
        self._running = True
        while self._running:
            try:
                text = await asyncio.wait_for(self._utterances.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            if self.on_committed:
                self.on_committed(text)

    async def stop(self) -> None:
        self._running = False
```

~35 lines. Ignores `inbound_queue` audio entirely. Fires `on_committed`
from injected text.

### Factory registration

```python
# voice_runtime/tts.py — add to create_tts()
if provider == "mock":
    from voice_runtime.mock.tts import MockTts
    return MockTts(**kwargs)

# voice_runtime/stt.py — add to create_stt()
if provider == "mock":
    from voice_runtime.mock.stt import MockStt
    return MockStt(**kwargs)
```

### Env var selection

Both consumers already resolve provider from `PROVIDER` or `TTS_PROVIDER`
env var, or pass it explicitly. Setting `VOICE_PROVIDER=mock` (or whatever
the consumer reads) selects mock providers with zero code changes.

### Consumer usage — ninchat_voice

```python
# In test — swap at services/tts.py factory call
with patch("services.tts.create_tts", return_value=mock_tts):
    # ... run FSM, inject events
    assert "Hei, kuinka voin auttaa" in mock_tts.spoken[0]
```

Or simpler — patch the module-level stub as test_full_call_flow.py already does:

```python
mock_tts = MockTts()
with patch("services.bridge_handlers.tts_speak", mock_tts.speak):
    # mock_tts.speak matches TtsProvider.speak signature exactly
    ...
```

### Consumer usage — outcaller

```python
# In test — swap the module-level singleton
mock_tts = MockTts()
with patch("projects.outcaller.nodes.tts._tts", mock_tts):
    # ... run graph
    assert "kiitos soitosta" in mock_tts.spoken[-1]
```

Or via env var — no patching at all:

```bash
VOICE_PROVIDER=mock pytest tests/e2e/
```

### Two-party simulation (outcaller ↔ ninchat_voice)

```python
nv_tts = MockTts()    # ninchat_voice speaks → outcaller hears
oc_tts = MockTts()    # outcaller speaks → ninchat_voice hears
nv_stt = MockStt()    # ninchat_voice listens
oc_stt = MockStt()    # outcaller listens

# Orchestrator wires them:
# When nv_tts.spoken grows → oc_stt.inject(latest)
# When oc_tts.spoken grows → nv_stt.inject(latest)
```

## Why Not NC-266

| Aspect | NC-266 (MockVoiceSession) | NC-267 (Mock Providers) |
|--------|---------------------------|-------------------------|
| Abstraction level | Session subclass | Provider implementations |
| Matches existing contracts | Yes (amended signatures match TtsProvider) | Yes (TtsProvider, SttProvider protocols) |
| Works for ninchat_voice | Yes (via bridge patches) | Yes (via factory or stub patches) |
| Works for outcaller | Partial (no bridge) | Yes (via singleton or factory patches) |
| Consumer code changes | Patching bridge_handlers | None (env var) or patching singletons |
| Factory support | Not applicable | Already exists (`create_tts`, `create_stt`) |
| Env var transparent mode | Not possible | `VOICE_PROVIDER=mock` |
| New surface area | MockVoiceSession class + methods | MockTts + MockStt (~55 lines total) |

## Constraints

1. **MockTts and MockStt live in voice_runtime/mock/** — reusable by all consumers
2. **No real provider imports** — mock must not import ElevenLabs, Azure, or Twilio
3. **Conforms to existing protocols** — `TtsProvider` and `SttProvider` from `voice_runtime/providers/__init__.py` (both already exist)
4. **No session subclassing** — VoiceSession is unchanged
5. **Deterministic** — no network calls; optional real-LLM mode for integration testing
6. **Fast** — each scenario completes in <2s without LLM
7. **CI-safe** — runs in GitHub Actions with no API keys

## Implementation Approach

### Phase 1: Mock providers in voice_runtime (~0.5 day)

1. Create `voice_runtime/mock/__init__.py`, `mock/tts.py`, `mock/stt.py`
2. `MockTts` implements `TtsProvider.speak()` — captures text, returns dict
3. `MockStt` implements `SttProvider` — fires `on_committed` from injected queue
4. Register `provider="mock"` in `create_tts()` and `create_stt()` factories
5. Protocol conformance test: verify MockTts/MockStt satisfy structural protocols

### Phase 2: Scripted conversation test in ninchat_voice (~0.5 day)

1. Use `MockTts` as drop-in for `tts_speak` patch (signature matches exactly)
2. Wire `MockStt.inject()` to feed scripted Finnish utterances
3. Marketing callback scenario: assert bot responses via `mock_tts.spoken`
4. Assert FSM reached expected final state

### Phase 3: Outcaller scripted test (~0.5 day)

1. Patch `_tts` and `_stt` singletons in outcaller's nodes
2. Probe-recap scenario: inject target answers, assert follow-ups
3. Or: `VOICE_PROVIDER=mock` for fully transparent mock

## Acceptance Criteria

1. `MockTts` in `voice_runtime/mock/tts.py` conforms to `TtsProvider` protocol
2. `MockStt` in `voice_runtime/mock/stt.py` conforms to `SttProvider` protocol
3. `create_tts(provider="mock")` returns `MockTts` instance
4. `create_stt(provider="mock")` returns `MockStt` instance
5. At least 1 scripted conversation test in ninchat_voice uses mock providers
6. Test asserts: (a) FSM reached expected state, (b) bot spoke ≥3 turns,
   (c) bot's first utterance contains greeting pattern
7. Test runs in <5s without LLM, <15s with real LLM
8. Mock providers usable by outcaller without ninchat_voice-specific imports

## Risks

| Risk | Mitigation |
|------|------------|
| Mock fidelity diverges from real providers | Protocol conformance test; real E2E scripts remain final gate |
| MockStt async start() loop complexity | Keep simple: poll queue with short sleep; no async sophistication |
| Factory env var accidentally used in production | `mock` is not a default; explicit opt-in only |
| Mark sync skipped in MockTts | Mock doesn't call `send_mark_and_wait()`; only affects barge-in timing tests which remain in real E2E |

## Relationship to NC-266 and NC-268

NC-267 supersedes NC-266. NC-266 marked `Superseded by NC-267`.

NC-268 is a prerequisite: outcaller must use `create_tts()` / `create_stt()`
factories instead of direct provider imports. Without NC-268, env-var
transparent mock selection (`VOICE_PROVIDER=mock`) doesn't work for outcaller.

The core goal is identical to NC-266 — scripted conversation testing at $0,
500+ scenarios in minutes. The difference is the mock boundary:
provider level (NC-267) vs session level (NC-266).

## Not in scope

- Replacing real E2E scripts — they remain for transport/provider integration
- Audio-level testing (TTS quality, STT accuracy, codec correctness)
- voice_runtime audit P0 fixes (independent; mock adapts when they land)
- Two-party simulation orchestrator (follow-up FR if needed)
