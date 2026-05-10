# NC-164: Multi-Provider STT with Secondary Logging

## Status: Draft

## Problem

When evaluating STT providers (ElevenLabs vs Azure vs future GCP), there is
no way to compare transcription quality in production without switching
providers and losing the baseline. A/B testing requires running both providers
on the same audio stream and comparing results offline.

Additionally, log output does not identify which STT/TTS provider is active,
making it difficult to correlate call quality with provider selection.

## Proposal

### 1. Secondary STT for Logging Only

Run a secondary STT provider alongside the primary. The secondary receives
the same audio frames but its transcripts are **logged only** — never routed
to the FSM or transcript queue.

```
inbound_queue ──┬──→ Primary STT ──→ transcript_queue (production)
                │
                └──→ Secondary STT ──→ log only (comparison)
```

**Configuration:**

```env
STT_PROVIDER=azure                    # primary — used for production
STT_SECONDARY_PROVIDER=elevenlabs     # logged only — for comparison
```

When `STT_SECONDARY_PROVIDER` is unset, no secondary runs (zero overhead).

**Architecture:**

- `voice_runtime/stt.py` factory gains `create_secondary_stt()` that wraps
  any provider with a logging-only adapter (no `_on_direct_dispatch`, no
  `transcript_queue` consumer)
- Audio tee in `_feed_audio()`: each frame written to both push streams
- Secondary transcripts logged at INFO level with `[SECONDARY]` prefix
- Secondary errors are logged but never propagate to caller

**Log format:**

```
INFO voice_runtime.providers.azure_stt: STT committed: "Punainen"
INFO voice_runtime.stt_secondary: [SECONDARY elevenlabs] committed: "Punainen."
```

### 2. Active Provider in Log Output

Add provider identification to startup and per-call log lines:

```
INFO server_fsm: 📞 Incoming call: sid=CA47c3... STT=azure TTS=azure
INFO voice_runtime.providers.azure_stt: AzurePersistentStt started (lang=fi-FI)
INFO voice_runtime.providers.azure_tts: AzureTTS speaking (voice=fi-FI-NooraNeural)
```

**Implementation:**
- `server_fsm.py` logs `STT={STT_PROVIDER} TTS={TTS_PROVIDER}` on incoming call
- Provider classes already log their class name; no change needed there

## Acceptance Criteria

- [ ] `STT_SECONDARY_PROVIDER` env var enables secondary STT
- [ ] Secondary receives same audio frames as primary
- [ ] Secondary transcripts logged at INFO, never routed to FSM
- [ ] Secondary errors logged at WARNING, never propagate
- [ ] Incoming call log line includes active STT and TTS provider names
- [ ] No performance impact when `STT_SECONDARY_PROVIDER` is unset
- [ ] Secondary provider lifecycle (start/stop) mirrors primary

## Non-Goals

- Consensus/voting between providers (future NC if needed)
- Secondary TTS (TTS comparison is audible — use manual A/B)
- Automatic provider failover (separate FR)

## Constraints

- Secondary must not affect primary latency or error handling
- Audio tee must be zero-copy where possible (both providers accept `bytes`)
- Secondary STT class reuse — same provider classes, different wiring
