# NC-167: Type STT/TTS Factories in VoiceSession

## Status: Absorbed into NC-165

## Problem

`VoiceSession` in `voice_runtime/session.py` stores provider factories and
instances as `Any`:

```python
stt: Any = field(default=None, repr=False)
tts: Any = field(default=None, repr=False)
stt_factory: Callable[[], Any] | None = field(default=None, repr=False)
stt_secondary_factory: Callable[[], Any] | None = field(default=None, repr=False)
```

Consumers (bridge_handlers.py, stt.py, twilio_ws.py) call methods on these
objects assuming a specific interface. The type checker cannot warn about
missing methods, wrong argument types, or incompatible providers.

This is distinct from NC-165 (Protocol definition) — this FR is specifically
about applying the Protocol to the session container and factory signatures.

**Affected files:**
- `voice_runtime/session.py:95–96` — `stt: Any`, `tts: Any`
- `voice_runtime/transports/twilio_ws.py:136–148` — calls `stt_factory()` and
  `stt_secondary_factory()` without return type knowledge
- `ninchat_voice/server_fsm.py:86–91` — assigns factories

## Proposal

After NC-165 defines `SttProvider` Protocol:

1. Type `stt` field: `stt: SttProvider | None = field(default=None, repr=False)`
2. Type factories: `stt_factory: Callable[[], SttProvider] | None`
3. Type secondary: `stt_secondary_factory: Callable[[], SttProvider] | None`
4. Define `TtsProvider` Protocol (simpler — just `speak()` and `stop()`)
5. Type `tts` field: `tts: TtsProvider | None`

## Acceptance Criteria

- [ ] `session.stt` typed as `SttProvider | None`
- [ ] `session.tts` typed as `TtsProvider | None`
- [ ] All factory fields use Protocol return types
- [ ] mypy/pyright passes on all consumers of session fields
- [ ] No runtime behavior change

## Dependencies

- NC-165 (SttProvider Protocol must exist first)

## Origin

Dual-STT hack analysis. `session.stt: Any` is the vector through which all
duck-typing hacks propagate — typing it is necessary but not sufficient
without NC-165's Protocol definition.
