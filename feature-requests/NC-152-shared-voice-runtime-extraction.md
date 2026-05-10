# NC-152: Shared Voice Runtime Extraction

**Status:** Conditionally Approved
**Date:** 2026-03-18
**Judged:** 2026-03-18
**Ref:** `docs/plan-fsm-3.md`, `docs/plan-forward.md` (Phase 3)

## Judgement Summary

Conditionally approved. The duplication is real (~80-85% functional overlap
verified), the three-layer architecture is sound, and scope discipline is
good. Phase 1 (audit) must be executed as a genuine deliverable before
coding begins — see [Phase 1 deliverables](#phase-1-deliverables).

### Corrective actions applied

1. **Line counts re-audited with `wc -l`.** Original estimates were
   inaccurate. All numbers below are actual.
2. **`audio.py` scope acknowledged** — audio_mixer is 228 lines including
   a G.711 μ-law codec, not a simple utility.
3. **Phase 1 deliverables made concrete** — session.py API sketch, TelcoSession
   split plan, and DGRAM bridge question must be answered before Phase 2.
4. **Phase 2 effort revised** — 2–2.5 days (was 1–1.5) to account for
   TelcoSession refactor complexity.

---

## Problem

The ElevenLabs + Twilio voice pipeline has been independently implemented
in two projects:

- **ninchat_voice** (1,714 lines): FSM-driven, DGRAM bridge architecture
- **outcaller** (1,405 lines): YAMLGraph-driven, in-process queue architecture

Both implement the same core: ElevenLabs TTS → ffmpeg → mulaw, ElevenLabs
Scribe STT, Twilio Media Streams WebSocket protocol, mark synchronization,
and call session lifecycle. A third consumer (voicebot-fsm) is planned.

The implementations differ only in plumbing — DGRAM sockets vs in-process
queues, FSM actions vs YAMLGraph tool nodes — but the core logic of
ElevenLabs API calls, ffmpeg transcoding, Twilio WebSocket handling, and
mulaw frame management is duplicated.

Additionally, the near-term roadmap includes:
- **Testing alternative TTS/STT providers** (Google Cloud TTS/Chirp,
  Azure Cognitive Services) for quality and cost comparison
- **SIP transport** (later) to replace Twilio for direct telephony

Extracting ElevenLabs + Twilio code as-is would create provider lock-in at
the package level. The extraction must separate provider-specific code from
the provider-agnostic session layer.

## Goal

Extract the shared voice pipeline into `projects/voice_runtime/` with a
three-layer architecture: provider-agnostic session coordination, pluggable
TTS/STT providers via factory functions, and pluggable transport modules.
Both ninchat_voice and outcaller become thin wrapper consumers.

## Architecture

### Three layers

```
┌─────────────────────────────────────────────┐
│  Consumer layer                              │
│  (FSM actions / YAMLGraph nodes / direct)    │
├─────────────────────────────────────────────┤
│  Session layer (provider-agnostic)           │
│  Queue coordinator, lifecycle, audio utils   │
├──────────┬──────────┬───────────────────────┤
│ ElevenLabs│ Google   │ Azure        TTS/STT  │
├──────────┴──────────┴───────────────────────┤
│ Twilio WS │ SIP (future)    Transport        │
└───────────┴─────────────────────────────────┘
```

### Factory pattern (mirrors `create_llm()`)

Provider selection uses the same factory pattern as YAMLGraph's LLM factory
(`yamlgraph/utils/llm_factory.py`):

```python
# voice_runtime/tts.py
def create_tts(provider: str = "elevenlabs", **kwargs):
    if provider == "elevenlabs":
        from voice_runtime.providers.elevenlabs_tts import ElevenLabsTTS
        return ElevenLabsTTS(**kwargs)
    raise ValueError(f"Unknown TTS provider: {provider}")
```

No Protocol classes, no abstract base classes, no plugin registry. The
factory function IS the interface. New providers are a new file + one
`elif`. Abstraction emerges from the second implementation, not speculation.

### Package structure

```
projects/voice_runtime/
  __init__.py              # Public API: create_tts(), create_stt(), create_transport()
  tts.py                   # Factory: create_tts(provider=...)
  stt.py                   # Factory: create_stt(provider=...)
  transport.py             # Factory: create_transport(provider=...)
  session.py               # Provider-agnostic session coordinator
  audio.py                 # Mulaw/PCM codec + mixing (from audio_mixer.py, 228 lines)
  providers/
    __init__.py
    elevenlabs_tts.py      # Current ElevenLabs TTS (merged from both projects)
    elevenlabs_stt.py      # Current Scribe STT (merged from both projects)
  transports/
    __init__.py
    twilio_ws.py           # Current Twilio Media Streams handler
```

Future additions are new files, not refactors:
```
  providers/
    google_tts.py          # Future: Google Cloud TTS
    google_stt.py          # Future: Google Chirp STT
    azure_tts.py           # Future: Azure Cognitive Services
  transports/
    sip.py                 # Future: SIP/RTP transport
```

## Scope

### Shared core (extract) — audited line counts

| Capability | ninchat_voice source | outcaller source | voice_runtime target |
|------------|---------------------|-----------------|---------------------|
| TTS pipeline | `services/tts.py` (212) | `nodes/tts.py` (138) | `providers/elevenlabs_tts.py` |
| STT pipeline | `services/persistent_stt.py` (263) | `nodes/stt.py` (185) | `providers/elevenlabs_stt.py` |
| Listen/barge-in | `services/stt.py` (116) | (in stt.py above) | (in elevenlabs_stt.py) |
| Twilio WebSocket | `services/bridge_listener.py` (165) | `server_base.py` (141) | `transports/twilio_ws.py` |
| Session coordinator | `services/telephony.py` (376) | `nodes/coordinator.py` (394) | `session.py` |
| Audio codec + mixer | — | `nodes/audio_mixer.py` (228) | `audio.py` |
| FSM action: speak | `voice_speak_action.py` (70) | — | (consumer wrapper) |
| FSM action: listen | `voice_listen_action.py` (68) | — | (consumer wrapper) |
| FSM action: yamlgraph | `yamlgraph_async_action.py` (294) | — | (consumer wrapper) |
| FSM action: preload | `yamlgraph_preload_action.py` (84) | — | (consumer wrapper) |
| FSM action: cleanup | `call_cleanup_action.py` (66) | — | (consumer wrapper) |
| Outbound call | — | `nodes/twilio_call.py` (209) | (consumer wrapper) |
| Inbound call | — | `nodes/twilio_inbound.py` (110) | (consumer wrapper) |
| **Totals** | **1,714** | **1,405** | |

### Stays in ninchat_voice (Ninchat-specific) — audited

| Module | Lines | Purpose |
|--------|-------|---------|
| `services/ninchat_session.py` | 239 | WebSocket bot client |
| `actions/real/ninchat_connect_action.py` | 49 | Connect to bot queue |
| `actions/real/ninchat_send_async_action.py` | 208 | Async bot query |
| **Total** | **496** | |

### Stays in outcaller (graph-specific)

| Module | Lines | Purpose |
|--------|-------|---------|
| `nodes/twilio_call.py` | 209 | Outbound call initiation + end_call |
| `nodes/twilio_inbound.py` | 110 | Inbound call await |
| `nodes/probe_recap.py` | varies | Questionnaire tool nodes |

### What moves vs. what wraps

The FSM actions (`voice_speak_action.py`, etc.) and YAMLGraph tool nodes
(`nodes/tts.py`, `nodes/stt.py`) are **consumer wrappers**, not core logic.
They stay in their respective projects and become thin delegates to
`voice_runtime`. The core extraction targets are:

- **TTS/STT providers**: ElevenLabs API calls + ffmpeg transcoding
- **Transport**: Twilio WebSocket protocol handler
- **Session**: Queue coordination, mark sync, lifecycle
- **Audio**: G.711 μ-law codec + mixing

## Key design decisions

**Best-of-both merge.** The two implementations have different strengths:
- ninchat_voice TTS: more mature (prebaked audio, barge-in handling)
- outcaller TelcoSession: cleaner (dataclass, explicit queue API)
- outcaller audio_mixer: substantial (228 lines, G.711 codec + mix algorithms)

The extracted core takes the best from each.

**Session coordinator has no provider imports.** `session.py` manages
queues, marks, and lifecycle. It receives audio chunks from a transport
and sends them to a transport. It doesn't know about ElevenLabs, Google,
Twilio, or SIP.

**Transport separated from session.** outcaller's `TelcoSession._run_loop`
currently starts the Twilio WebSocket server — this must be separated so
SIP can be added as an alternative transport without touching session code.
See [Phase 1 deliverables](#phase-1-deliverables) for the required split
plan.

**Session as constructor param, not global.** outcaller's `_active_session`
module-level global becomes a constructor parameter.

## Phase 1 deliverables

Phase 1 is a **genuine deliverable**, not a rubber stamp. It must produce:

### 1. Corrected overlap audit

Map the exact functional overlap between both implementations. The line
counts above are accurate; now determine which lines are duplicated logic
vs. project-specific plumbing.

### 2. `session.py` API sketch

The session coordinator is the central artifact. At minimum, define:

```python
class VoiceSession:
    """Provider-agnostic call session coordinator."""

    # Queue API (from outcaller's TelcoSession dataclass pattern)
    inbound: asyncio.Queue[bytes | None]   # audio from caller
    outbound: asyncio.Queue[bytes]          # audio to caller

    def put_inbound(self, data: bytes | None) -> None: ...
    def put_outbound(self, data: bytes) -> None: ...
    async def get_inbound(self) -> bytes | None: ...
    async def get_outbound(self) -> bytes: ...
    def clear_inbound(self) -> None: ...

    # Mark sync (from outcaller's send_mark_and_wait pattern)
    def send_mark_and_wait(self, mark_name: str, timeout: float) -> None: ...
    def signal_mark_received(self, mark_name: str) -> None: ...

    # Lifecycle
    def signal_disconnected(self) -> None: ...
    @property
    def is_disconnected(self) -> bool: ...
    def shutdown(self) -> None: ...

    # Audio monitoring (from outcaller's AudioMixer integration)
    def tap_caller(self, chunk: bytes) -> None: ...
    def tap_agent(self, chunk: bytes) -> None: ...
```

Key question: does `VoiceSession` own the asyncio event loop and server
startup (as outcaller's TelcoSession does), or does the consumer provide
the loop? The answer determines whether `session.py` is 100 lines or 300.

### 3. TelcoSession split plan

outcaller's `TelcoSession` (394 lines) interleaves:
- **Session logic**: queues, marks, disconnect signaling, audio mixer (~200 lines)
- **Transport logic**: uvicorn server startup, event loop management (~100 lines)
- **Consumer logic**: `_active_session` global, `start_with_app()` factory (~90 lines)

Document which methods go where:
- `session.py`: queue API, mark sync, lifecycle, monitoring hooks
- `transports/twilio_ws.py`: WebSocket protocol, uvicorn startup
- Consumer wrapper: session registry, app factory

### 4. DGRAM bridge question

ninchat_voice's architecture is fundamentally different: FSM actions run in
a subprocess and communicate with services via Unix DGRAM sockets. The
bridge_listener (165 lines) is a DGRAM server that receives commands and
dispatches to TTS/STT services.

Two options:
- **Option X**: ninchat_voice uses `session.py` + `voice_runtime` providers.
  The DGRAM bridge stays as a ninchat_voice-specific transport adapter that
  translates DGRAM commands into `VoiceSession` method calls.
- **Option Y**: ninchat_voice only imports the provider modules
  (`elevenlabs_tts.py`, `elevenlabs_stt.py`) and keeps its own bridge +
  session management. The session layer is outcaller-pattern only.

Option X is cleaner (one session implementation) but requires proving that
`VoiceSession` works across process boundaries. Option Y is pragmatic but
means ninchat_voice doesn't fully benefit from the extraction.

**This question must be answered in Phase 1.**

## Phases (revised, updated with actuals)

### Phase 1: Audit and boundary definition — DONE

Delivered four artifacts in `docs/nc152-phase1-audit.md`. Gate passed.

### Phase 2: Extract — IN PROGRESS

Phase 2 was split into two sub-steps:

#### Phase 2a: Extract voice_runtime package (TDD) — DONE

Created `projects/voice_runtime/` with 1,083 lines of production code and
1,040 lines of tests (79 passing, 1.4s).

| Module | Lines | Source |
|--------|-------|--------|
| `session.py` | 245 | Merged from both TelcoSessions; ~150 lines estimated, actual ~245 including docstrings |
| `audio.py` | 206 | Extracted from outcaller `audio_mixer.py` as-is |
| `providers/elevenlabs_tts.py` | 128 | Merged: ninchat_voice barge-in + outcaller monitoring tap |
| `providers/elevenlabs_stt.py` | 303 | Both modes: `PersistentSttSession` (ninchat_voice base) + `PerTurnStt` (outcaller pattern) |
| `transports/twilio_ws.py` | 137 | Merged: provider-agnostic, no STT/ElevenLabs imports |
| `tts.py` | 17 | Factory: `create_tts(provider=...)` |
| `stt.py` | 27 | Factory: `create_stt(provider=..., mode=...)` |
| `transport.py` | 20 | Factory: `create_transport(provider=...)` |

Verified:
- [x] No ninchat_voice or outcaller imports (only in docstring references)
- [x] `session.py` has no provider or transport imports
- [x] Factory functions instantiate correct classes
- [x] outcaller unit tests: 174 passed (unaffected)
- [x] ninchat_voice unit tests: 187 passed (1 pre-existing failure unrelated)

**What went right:**
- TDD approach worked well — 79 tests written before implementation, all
  green on first pass after implementation (4 test patches needed for
  ElevenLabs lazy import mocking, fixed in minutes).
- `audio.py` extracted truly as-is — zero modifications needed.
- `session.py` ended up at 245 lines (estimated ~150). The difference is
  docstrings and the `signal_ws_connected` loop-capture logic that was
  underestimated in the API sketch.
- The hardest anticipated part — TelcoSession asyncio event loop separation —
  turned out clean because `VoiceSession` correctly does NOT own the loop.
  Transport startup (`_run_loop`, uvicorn, `create_app`) stays in consumers.

**What went wrong:**
- `elevenlabs_stt.py` at 303 lines exceeds the 400-line target for the
  combined file. Two classes in one file is fine at this size, but if
  `PersistentSttSession` grows further (new provider features), consider
  splitting into two files.
- The ninchat_voice `twilio_ws.py` has STT lifecycle coupling (starts
  `PersistentSttSession` on stream start, sends Twilio clear events for
  barge-in). The extracted `transports/twilio_ws.py` is clean but does NOT
  include these features. ninchat_voice's consumer wrapper must add them
  back. This is the right split (provider-agnostic transport), but it means
  the consumer wrapper is not trivially thin.

#### Phase 2b: Adapt consumers — split to NC-153

Separated into its own feature request: **NC-153** (`NC-153-voice-runtime-
consumer-adaptation.md`). Rationale: Phase 2a is a self-contained deliverable
(new package, own tests, no consumer changes). Phase 2b modifies two existing
projects with different risk profiles. Separate FRs allow independent review
and judgement.

Replace consumer implementations with imports from `voice_runtime`:

**ninchat_voice:**
- `services/tts.py` (212 lines) → thin wrapper calling `ElevenLabsTTS.speak()`
  plus predefined audio lookup (ninchat_voice-specific)
- `services/persistent_stt.py` (263 lines) → import from `voice_runtime`;
  inject `_fsm_sender` and `_on_direct_transcribed` callbacks
- `services/stt.py` (116 lines) → thin wrapper calling persistent STT
- `services/telephony.py` (376 lines) → import `VoiceSession`; keep
  `start()`, `_run_loop()`, `request_close_ws()`, `schedule_twilio_clear()`,
  session registry as consumer-specific code
- `services/twilio_ws.py` (204 lines) → import base handler from
  `voice_runtime`; add STT lifecycle, clear events, watch_close as consumer
  extensions

**outcaller:**
- `nodes/tts.py` (138 lines) → thin wrapper: `get_active_session()` +
  `ElevenLabsTTS.speak()` + `[DONE]` marker handling
- `nodes/stt.py` (185 lines) → thin wrapper: `get_active_session()` +
  `PerTurnStt.listen()`
- `nodes/coordinator.py` (394 lines) → import `VoiceSession`; keep
  `start()`, `start_with_app()`, `_run_loop()`, `_validate_monitor()`,
  session registry as consumer-specific code
- `nodes/audio_mixer.py` (228 lines) → re-export from `voice_runtime.audio`
  or update imports in coordinator
- `server_base.py` (141 lines) → import `register_voice_websocket` from
  `voice_runtime`

**Risk:** ninchat_voice's `twilio_ws.py` has significant extensions beyond
the base transport (persistent STT lifecycle, clear events, watch_close).
The consumer wrapper cannot simply delegate — it must compose the base
handler with its extensions. Two options:
- Option A: ninchat_voice keeps its own `twilio_ws.py` that imports and
  extends the base handler
- Option B: The base handler accepts optional lifecycle hooks (on_start,
  on_stop callbacks)

Decision deferred to Phase 2b execution.

### Phase 3: Consumer validation (0.5 day)

- Import `voice_runtime` from voicebot-fsm
- Run smoke test: TTS + STT + WebSocket over a test call
- Document the wrapper contract and factory usage
- Verify that adding a hypothetical `google_tts.py` requires only a new
  file + one `elif` in the factory

## Acceptance Criteria

- [x] `projects/voice_runtime/` exists with no ninchat/outcaller imports
- [x] Factory functions work: `create_tts("elevenlabs")`, `create_stt("elevenlabs")`, `create_transport("twilio")`
- [x] `session.py` has no provider or transport imports
- [x] `session.py` API is documented with method signatures
- [ ] ninchat_voice: all existing tests pass (187 unit + E2E)
- [ ] outcaller: all existing tests pass
- [ ] Both projects import from `voice_runtime`, not their own copies
- [ ] Adding a new provider requires only a new file + one `elif`
- [ ] voicebot-fsm can import and use `voice_runtime`
- [ ] Wrapper contract documented (FSM action wrapper, tool node wrapper)

## Effort

**Estimated: 3.5–4 days** (revised with actuals)

- Phase 1 (audit + API design): 1 day — **actual: 1 day**
- Phase 2a (extract voice_runtime, TDD): 1 day — **actual: ~0.5 day**
- Phase 2b (adapt consumers): 1–1.5 days — **not started**
- Phase 3 (validation + docs): 0.5 day

## Not in scope

- Multi-FSM split (engine FRs for `fire_and_forget` + `guard` on `send_event`)
- Multi-topic routing or intent classification
- Auth gating
- FSM config deduplication (YAML configs don't support imports)
- Implementing Google/Azure providers (extraction enables them; they are
  separate work items)
- Implementing SIP transport (extraction enables it; separate work item)

These are documented in `docs/plan-fsm-3.md` with forcing functions.
