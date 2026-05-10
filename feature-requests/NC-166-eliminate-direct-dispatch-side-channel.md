# NC-166: Move Processing Decisions from voice_runtime to Consumer

## Status: Approved

## Problem

voice_runtime currently makes routing and barge-in decisions that belong to
the consumer:

1. **STT providers decide routing** — `_on_committed()` checks `_listening`,
   `_speaking`, `_direct_sent`, and routes to either `_transcript_queue` or
   `_on_direct_dispatch` callback. These are policy decisions that differ per
   consumer (ninchat_voice vs outcaller).

2. **STT providers decide barge-in** — `_on_partial()` checks `_speaking` +
   `len(text) > 2` and fires `_barge_in_event`. This is an automatic TTS
   interruption policy that should be the FSM's job.

3. **Callback injection breaks encapsulation** — `bridge_handlers.py` reaches
   into `stt._on_direct_dispatch` and `stt._on_direct_transcribed` to inject
   lambdas. No interface contract, no Protocol coverage.

4. **outcaller cannot participate** — it uses `PerTurnStt`, has no barge-in,
   and no way to receive committed text between turns. The `_on_committed`
   routing logic is hardwired to ninchat_voice's FSM pattern.

**Root cause:** voice_runtime absorbed consumer-specific policy. The One Law
says: normalize at the boundary where external data enters. STT providers
should normalize audio → text. Routing text to FSM/queue is consumer policy.

## Revised Architecture

### Principle: voice_runtime provides interfaces, consumers make decisions

```
BEFORE (voice_runtime decides):
  STT._on_partial(text) → auto barge-in (len > 2 + speaking)
  STT._on_committed(text) → route: queue OR direct-dispatch
  Consumer: arms barge-in, bridges events, passes stop_event

AFTER (consumer decides):
  STT._on_committed(text) → callback to consumer (always)
  Consumer: receives ALL committed text, decides action
  voice_runtime: provides stop_tts() interface
  Barge-in via partials: DEFERRED (Phase 2)
```

### What stays in voice_runtime (boundary normalization)

- **Echo discard** — acoustic concern, not policy. Keep `set_speaking()` and
  `_discard_until` in providers. Committed text that passes echo discard
  fires the callback.
- **TTS stop interface** — voice_runtime exposes a method for consumers to
  stop TTS playback. No automatic decisions.
- **Audio fan-out** — SttTee relays audio to both providers. Secondary
  handles its own committed text (logging only).

### What moves to consumer (policy decisions)

- **Routing** — ninchat_voice decides: queue (listen mode) or dispatch
  (between turns). outcaller decides: always queue.
- **TTS interruption** — FSM or consumer decides when to call stop_tts(),
  not STT partial text heuristic.
- **Transcript recording** — consumer-side concern, not a callback injected
  into STT provider internals.

## Phase 1: `_on_committed` callback + stop_tts interface

### voice_runtime changes

**STT providers (azure_stt.py, elevenlabs_stt.py):**
- Keep: `set_speaking()`, echo discard, `start()`, `stop()`
- Add: single `on_committed: Callable[[str], None] | None` — fires for
  every committed utterance that passes echo discard
- Remove: `_on_direct_dispatch`, `_on_direct_transcribed`, `_direct_sent`,
  `_listening`, `arm_barge_in()`, `_barge_in_event`
- Remove: all routing logic from `_on_committed()` — just fire callback

```python
# Simplified _on_committed — no routing decisions
def _on_committed(self, evt: Any) -> None:
    text = evt.result.text
    if self._speaking:
        return
    if time.monotonic() < self._discard_until:
        return
    cleaned = text.strip()
    if not cleaned:
        return
    if self.on_committed:
        self.on_committed(cleaned)
```

**TTS stop interface:**
- Option A: `session.stop_tts()` method on VoiceSession
- Option B: Consumer holds `threading.Event` and calls `.set()` directly
- Recommendation: Option B (consumer already creates the event in
  bridge_handlers.py — just make it explicit, no new abstraction needed)

**SttProvider Protocol update:**
- Remove: `_on_direct_dispatch`, `_on_direct_transcribed`, `arm_barge_in()`
- Add: `on_committed: Callable[[str], None] | None`
- Keep: `set_speaking()`, `start()`, `stop()`, `next_transcript()`

**SttTee:**
- Remove: `_on_direct_dispatch`, `_on_direct_transcribed` proxy properties
- Add: relay `on_committed` setter to primary only

### ninchat_voice changes

**bridge_handlers.py:**
- Remove: `stt._on_direct_dispatch = lambda` injection
- Remove: `stt._on_direct_transcribed = _record_direct_transcribed` injection
- Add: set `session.stt.on_committed = _handle_committed` with routing logic:

```python
def _handle_committed(text: str) -> None:
    """Consumer-side routing for committed text."""
    # Record transcript (was _on_direct_transcribed)
    transcript.append(source="user", kind="utterance", text=text, ...)

    if mode == "listen":
        # Queue for listen loop (was _transcript_queue path)
        transcript_queue.put_nowait(text)
    else:
        # Dispatch to FSM (was _on_direct_dispatch path)
        sender.send_event("transcribed", {"user_utterance": text})
```

- Barge-in: remove `arm_barge_in()` + `_signal_interrupt()` bridge.
  TTS interruption deferred to Phase 2.

### outcaller changes

**nodes/stt.py or coordinator:**
- Set `session.stt.on_committed = _queue_response` where:

```python
def _queue_response(text: str) -> None:
    """Queue committed text for next processing step."""
    response_queue.put_nowait(text)
```

- outcaller always queues — no direct dispatch, no barge-in.

## Phase 2: Partial-text barge-in (DEFERRED)

- Add `on_partial: Callable[[str], None] | None` callback
- Consumer decides barge-in policy (text length? confidence? always?)
- Consumer calls `stop_event.set()` when it decides to interrupt TTS
- voice_runtime provides the plumbing, consumer provides the policy

## Acceptance Criteria

### Phase 1
- [ ] `_on_direct_dispatch` removed from all STT providers
- [ ] `_on_direct_transcribed` removed from all STT providers
- [ ] `_direct_sent` flag removed from all STT providers
- [ ] `_listening` flag removed from routing (stays as consumer-side state)
- [ ] `arm_barge_in()` removed from all STT providers
- [ ] `_barge_in_event` removed from all STT providers
- [ ] `_on_partial()` removed from all STT providers
- [ ] Single `on_committed` callback on STT providers — fires unconditionally
- [ ] SttProvider Protocol updated (simpler: fewer members)
- [ ] `AzurePerTurnStt` class deleted (absorbed into `AzurePersistentStt`)
- [ ] `PerTurnStt` class deleted (absorbed into `PersistentSttSession`)
- [ ] ninchat_voice: routing logic in bridge_handlers, not in STT provider
- [ ] outcaller: per-turn wrapper using persistent provider + `on_committed`
- [ ] SttTee updated: relay `on_committed` to primary only
- [ ] No regression: utterance during listen → FSM receives event
- [ ] No regression: utterance between turns → FSM receives event
- [ ] No regression: outcaller listen → returns transcript

### Phase 2 (deferred)
- [ ] `on_partial` callback on STT providers
- [ ] Consumer-side barge-in policy
- [ ] TTS interruption via consumer-controlled stop_event

## Constraints

- Echo discard stays in voice_runtime (acoustic boundary, not policy)
- Phase 1 disables partial-text barge-in — TTS plays to completion unless
  consumer implements its own stop logic via stop_event
- `next_transcript()` may be removed or simplified — consumer controls
  the queue now
- SttTee secondary still logs only (no `on_committed` relay to secondary)

## Implementation Order

1. Update SttProvider Protocol (remove 5 members, add `on_committed`)
2. Simplify `_on_committed()` in azure_stt.py and elevenlabs_stt.py
3. Remove barge-in plumbing (`arm_barge_in`, `_barge_in_event`, `_on_partial`)
4. Delete `AzurePerTurnStt` and `PerTurnStt` classes
5. Update SttTee
6. Move routing logic to ninchat_voice bridge_handlers.py
7. Create outcaller per-turn wrapper using persistent provider
8. Update tests

## Origin

Dual-STT hack analysis (diary-2026-03-20-nc161-nc164). Revised after
NC-166 judgement (rejected: timing constraint kills unified queue when
reader is idle during TTS). New approach: don't change the data flow,
change who makes the routing decision.

---

## Judgement (2026-03-20)

**Verdict: APPROVED with 3 amendments.**

The direction is sound — moving routing policy out of voice_runtime providers
to consumers is the correct architectural fix. The One Law argument holds:
STT providers normalize audio → text, consumers route text to destinations.

### Verified claims

| Claim | Finding |
|-------|---------|
| `_listening` only used for routing | ✅ Only read in `_on_committed()`, toggled in `next_transcript()` |
| `_on_partial()` is 100% barge-in | ✅ Can be deleted entirely when barge-in removed |
| `set_speaking()` works without barge-in | ✅ Lifecycle independent: `True` before TTS, `False` after |
| outcaller uses callback-only pattern | ✅ `PerTurnStt` already proves the pattern works |

### Amendment 1: Resolve `next_transcript()` / queue contradiction

The plan says keep `next_transcript()` in the Protocol, but also says
`on_committed` callback replaces all routing. These are incompatible.

ninchat_voice's listen loop depends on the queue:
```
bridge_handlers._on_listen() → stt.listen() → _next_stable_transcript()
  → stt.next_transcript() → await _transcript_queue.get()
```

`_next_stable_transcript()` calls `next_transcript()` **twice** (stability
grace window). This cannot work with pure callbacks.

**Required resolution:** Keep `_transcript_queue` and `next_transcript()` in
voice_runtime as implementation detail. The `on_committed` callback fires
from the provider, and the **consumer decides** whether to enqueue (for
listen mode) or dispatch (for between-turns mode):

```python
def _handle_committed(text: str) -> None:
    if listening:
        stt._transcript_queue.put_nowait(text)  # consumer pushes to queue
    else:
        sender.send_event("transcribed", ...)   # consumer dispatches
```

The queue becomes a consumer-managed buffer, not a provider-managed router.
`next_transcript()` stays as a read interface. Provider no longer writes to
the queue — consumer does.

Update acceptance criteria: replace "`_listening` flag removed from all STT
providers" with "`_listening` flag removed from all STT providers' internal
routing; queue still exists as consumer-writable buffer".

### Amendment 2: Specify thread safety contract

`on_committed` fires from the Azure SDK recognizer thread (not asyncio).
The current `_on_direct_dispatch` already fires from this thread — so the
existing contract is "callback must be thread-safe".

**Required:** Add explicit constraint: "The `on_committed` callback is
invoked from the STT provider's recognizer thread. Consumers must ensure
their callback is thread-safe (e.g., `FsmEventSender.send_event()` uses Unix
DGRAM socket which is inherently thread-safe; `asyncio.Queue.put_nowait()`
is NOT thread-safe from non-asyncio threads — use
`loop.call_soon_threadsafe()`)."

This is not a new risk (same thread safety applies to `_on_direct_dispatch`
today), but it must be documented since the callback is now part of the
public interface.

### Amendment 3: Unify PerTurnStt into persistent providers (revised)

~~Original: "PerTurnStt is out of scope."~~

**Revised:** PerTurnStt is absorbed client functionality. `AzurePerTurnStt`
and `AzurePersistentStt` wrap the same Azure Speech SDK. `PerTurnStt` and
`PersistentSttSession` wrap the same ElevenLabs Scribe API. They duplicate
connection setup, audio format config, and error handling. The only
difference is lifecycle management — which is a client decision, not a
provider distinction.

One STT provider per engine. Client controls lifecycle:

```
ONE provider per engine (voice_runtime):
  start(queue)              → opens connection
  stop()                    → closes connection
  on_committed(text)        → fires callback
  set_speaking(bool)        → echo discard

Per-turn client (outcaller):       Persistent client (ninchat_voice):
  provider.start(queue)              provider.start(queue)    # call start
  await first on_committed           # lives across turns
  provider.stop()                    provider.stop()          # call end
```

**Required:**
- Delete `AzurePerTurnStt` class from `azure_stt.py`
- Delete `PerTurnStt` class from `elevenlabs_stt.py`
- outcaller uses `AzurePersistentStt` or `PersistentSttSession` with a
  client-side wrapper that calls `start()`, awaits first `on_committed`,
  then calls `stop()`
- Per-turn convenience helper lives in outcaller (client code), not in
  voice_runtime (provider code)

This is Phase 1 scope — the `on_committed` callback enables the
unification directly.

### Updated scope after amendments

**Phase 1 removes from voice_runtime STT providers:**
- `_on_direct_dispatch`, `_on_direct_transcribed`, `_direct_sent` (routing)
- `arm_barge_in()`, `_barge_in_event`, `_on_partial()` (barge-in)
- All routing logic from `_on_committed()` (provider no longer decides)
- `AzurePerTurnStt` class (absorbed into `AzurePersistentStt`)
- `PerTurnStt` class (absorbed into `PersistentSttSession`)

**Phase 1 keeps in voice_runtime STT providers:**
- `_transcript_queue` (consumer-writable buffer, reads via `next_transcript()`)
- `set_speaking()` (echo discard boundary)
- `on_committed` callback (new, fires unconditionally after echo discard)

**Phase 1 moves to consumers:**
- ninchat_voice: routing decision (listen → queue, between turns → dispatch)
- ninchat_voice: transcript recording (was `_on_direct_transcribed`)
- outcaller: per-turn lifecycle wrapper (start → await first committed → stop)

**Phase 2 (deferred):**
- Barge-in via partials (`on_partial` callback, consumer-side policy)

**Freeze scope. Grant authority.**

---

## Second Judgement (2026-03-20, post-Amendment 3 revision)

**Verdict: APPROVED with 4 refinements.**

The revised scope (include outcaller, delete PerTurnStt classes) is
architecturally correct. One provider per engine, client controls lifecycle.
The `on_committed` callback enables this directly. Four issues to address:

### Refinement 1: Move queue to consumer entirely

Amendment 1 said keep `_transcript_queue` and `next_transcript()` in the
provider as "consumer-writable buffer". This is architecturally incoherent —
the provider owns a queue it never writes to, and exposes `next_transcript()`
as a read interface for a buffer managed by external code.

**Fix:** Remove `_transcript_queue` and `next_transcript()` from providers.
ninchat_voice creates its own `asyncio.Queue` and manages it in the
`on_committed` callback:

```python
# ninchat_voice — consumer owns the queue
listen_queue: asyncio.Queue[str] = asyncio.Queue()

def _handle_committed(text: str) -> None:
    if listening:
        loop.call_soon_threadsafe(listen_queue.put_nowait, text)
    else:
        sender.send_event("transcribed", {"user_utterance": text})
```

The listen loop (`_next_stable_transcript`) reads from `listen_queue`
instead of `stt.next_transcript()`. This fully separates concerns: provider
produces text via callback, consumer manages its own buffering.

Update SttProvider Protocol: remove `next_transcript()`.

### Refinement 2: `_listening` toggle moves to consumer

With the queue in the consumer, `_listening` (which gates routing in the
callback) is also consumer state. The provider no longer needs it.

The `_listening` setter currently resets `_direct_sent = False` (NC-163
fix). With `_direct_sent` deleted (per plan), this coupling disappears.

### Refinement 3: Barge-in regression is a non-issue

Audit found barge-in is **disabled by default** (`NINCHAT_VOICE_MODE=simple`).
It's only active when env var is explicitly set to `"bargein"`. The default
production mode already plays TTS to completion.

Phase 1 removing `arm_barge_in()` / `_on_partial()` is equivalent to the
current default behavior. Phase 2 restores barge-in for the `bargein` mode
via consumer-side `on_partial` callback.

No production regression.

### Refinement 4: Implementation order — create before deleting

Current order says step 4 "Delete `AzurePerTurnStt` and `PerTurnStt`" before
step 7 "Create outcaller per-turn wrapper". This creates a broken window
where outcaller has no STT.

**Fix:** Swap ordering:

1. Update SttProvider Protocol (remove routing/barge-in, add `on_committed`)
2. Simplify `_on_committed()` in azure_stt.py and elevenlabs_stt.py
3. Remove barge-in plumbing (`arm_barge_in`, `_barge_in_event`, `_on_partial`)
4. Remove `_transcript_queue` and `next_transcript()` from providers
5. Update SttTee
6. Move routing logic to ninchat_voice bridge_handlers.py (with consumer queue)
7. Create outcaller per-turn wrapper using persistent provider
8. Delete `AzurePerTurnStt` and `PerTurnStt` classes
9. Update tests

### Updated SttProvider Protocol (post-refinements)

```python
class SttProvider(Protocol):
    on_committed: Callable[[str], None] | None

    def set_speaking(self, speaking: bool) -> None: ...
    async def start(self, inbound_queue: asyncio.Queue[bytes | None]) -> None: ...
    async def stop(self) -> None: ...
```

4 members. Down from 7 (NC-165) and originally 5+2 callbacks.

**Grant authority. Proceed to enforce.**
