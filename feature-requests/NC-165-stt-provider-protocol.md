# NC-165: SttProvider Protocol and Typed Session Fields

## Status: Done

*Absorbs NC-167 (Type STT/TTS Factories in VoiceSession).*
*Commits: `8905182` (RED, 14 tests), `99f30f4` (GREEN, Protocol + typed fields).*

## Problem

The STT provider interface is entirely duck-typed. `session.stt` is typed as
`Any` in `voice_runtime/session.py`. Consumers in `bridge_handlers.py` access
private attributes (`_on_direct_dispatch`, `_on_direct_transcribed`) and
methods (`set_speaking()`, `arm_barge_in()`, `next_transcript()`) without any
compile-time enforcement.

This caused a live-call `AttributeError` in NC-161 when `AzurePersistentStt`
was missing `_on_direct_dispatch` — an error invisible to unit tests because
they don't exercise the full bridge→STT→FSM path. The error was caught only
on a production call.

The same `Any` typing affects session fields and factory return types,
meaning the type checker cannot warn about missing methods, wrong arguments,
or incompatible providers anywhere in the chain.

**Affected files:**
- `voice_runtime/session.py:95–96` — `stt: Any`, `tts: Any`, factories `Callable[[], Any]`
- `voice_runtime/transports/twilio_ws.py:136–148` — calls factories without return type
- `bridge_handlers.py:189–212` — accesses `_on_direct_dispatch`, `_on_direct_transcribed`
- `bridge_handlers.py:212–248` — calls `set_speaking()`, `arm_barge_in()`
- `stt.py:33–50` — calls `next_transcript()`
- `stt_tee.py` — proxies all of the above
- `azure_stt.py`, `elevenlabs_stt.py` — implement the implicit contract
- `ninchat_voice/server_fsm.py:86–91` — assigns factories

## Proposal

### 1. Define SttProvider Protocol

Add to `voice_runtime/providers/__init__.py`:

```python
from typing import Protocol, Callable
import asyncio


class SttProvider(Protocol):
    _on_direct_dispatch: Callable[[str], None] | None
    _on_direct_transcribed: Callable[[str], None] | None

    def set_speaking(self, speaking: bool) -> None: ...
    def arm_barge_in(self) -> asyncio.Event: ...
    async def next_transcript(self, timeout: float = 30.0) -> str | None: ...
    async def start(self, inbound_queue: asyncio.Queue[bytes | None]) -> None: ...
    async def stop(self) -> None: ...
```

**Excluded from Protocol** (internal implementation, not consumer-facing):
- `_transcript_queue` — consumers access via `next_transcript()`, not directly.
  Azure uses `Queue[str | None]`, ElevenLabs uses `Queue[str]` — invariant
  type mismatch makes this impossible to unify in Protocol.
- `_listening` — set internally by `next_transcript()`, never accessed by
  consumers. SttTee proxies it but that's adapter internals.
- `_direct_sent` — internal state gate, never accessed externally.

**Included** (consumer-facing, set by bridge_handlers.py):
- `_on_direct_dispatch` — writable by consumers (callback injection)
- `_on_direct_transcribed` — writable by consumers (callback injection)

### 2. Type VoiceSession fields (absorbed from NC-167)

```python
stt: SttProvider | None = field(default=None, repr=False)
stt_factory: Callable[[], SttProvider] | None = field(default=None, repr=False)
stt_secondary_factory: Callable[[], SttProvider] | None = field(default=None, repr=False)
```

### 3. Verify provider conformance

- `AzurePersistentStt` — must satisfy `SttProvider`
- `PersistentSttSession` (ElevenLabs) — must satisfy `SttProvider`
- `SttTee` — must satisfy `SttProvider` (proxies to primary)

## Acceptance Criteria

- [ ] `SttProvider` Protocol defined in `voice_runtime/providers/__init__.py`
- [ ] `session.stt` typed as `SttProvider | None`
- [ ] `stt_factory` typed as `Callable[[], SttProvider] | None`
- [ ] `stt_secondary_factory` typed as `Callable[[], SttProvider] | None`
- [ ] `SttTee`, `AzurePersistentStt`, `PersistentSttSession` all satisfy Protocol
- [ ] pyright/mypy passes with `SttProvider` typing — all consumer call sites clean
- [ ] No runtime behavior change

## Constraints

- Protocol, not ABC — no inheritance requirement on providers
- `_on_direct_dispatch` and `_on_direct_transcribed` in Protocol is intentional:
  consumers write to them (bridge_handlers.py:192, 211)
- Does not change runtime behavior — type-only change
- No `@runtime_checkable` / `isinstance()` tests — type-checker enforcement
  is the goal, not runtime checks

## Judgement amendments applied

1. Removed `_transcript_queue` from Protocol (invariant Queue type mismatch)
2. Removed `_listening` from Protocol (internal, not consumer-accessed)
3. Replaced `isinstance` acceptance criterion with pyright/mypy verification
4. Merged NC-167 scope (session field typing) into this FR

## Origin

Dual-STT hack analysis (diary-2026-03-20-nc161-nc164). NC-161 live call
`AttributeError` proved duck-typing insufficient for safety-critical path.
