# NC-161: Azure Speech Provider (STT + TTS)

**Status:** Approved
**Date:** 2026-03-19
**Ref:** `docs/voice-runtime-provider-alternatives.md` (Section 4),
         NC-152 (shared voice_runtime extraction),
         NC-159 (ElevenLabs native mulaw — prerequisite),
         NC-160 (Google Cloud Speech provider — complementary)

## Problem

voice_runtime supports only ElevenLabs as STT/TTS provider — a single
point of failure. The NC-152 extraction architecture was designed for
provider swapping via factory functions, but only one provider exists.

Azure Speech Service is the **strongest alternative for persistent STT
mode** — no streaming duration limit, push stream maps directly to the
inbound queue pattern, and configurable VAD matches the commit trigger
needs. The SDK event model (`recognizing`/`recognized`) maps cleanly to
the existing `_on_partial`/`_on_committed` callbacks.

For TTS, Azure provides native mulaw 8kHz output and adequate Finnish
neural voices, though with higher latency than ElevenLabs (~200-400ms
vs ~75-135ms TTFA). This makes Azure TTS better suited for structured
questionnaire flows than open conversation, but viable as a fallback.

NC-160 (GCP) covers per-turn STT as the simpler first provider. This FR
covers Azure as the **persistent STT provider** (highest production value)
and a TTS fallback.

## Research Summary

### Azure Text-to-Speech

| Aspect | Detail |
|--------|--------|
| API | Native SDK (`azure-cognitiveservices-speech`), event-based |
| Audio output | **Native `Raw8Khz8BitMonoMULaw`** — no ffmpeg needed |
| Streaming | `synthesizing` event callback fires with audio chunks during synthesis |
| Barge-in | `stop_speaking_async()` cancels synthesis mid-stream |
| Finnish voices | 3 neural voices: `fi-FI-SelmaNeural` (Female), `fi-FI-HarriNeural` (Male), `fi-FI-NooraNeural` (Female) |
| Multilingual | `en-US-JennyMultilingualNeural` and `en-US-RyanMultilingualNeural` support Finnish (`fi-FI`) among 26 languages |
| Latency | **200-400ms TTFA** (vs ElevenLabs ~75-135ms) — 2-3x higher |
| Auth | Subscription key + region (e.g., `westeurope`), or Azure AD token |
| Pricing | ~$15/1M chars (neural), enterprise volume discounts available |
| Library | `azure-cognitiveservices-speech` (~50MB native C++ binary, platform-specific wheels) |
| SSML | Full SSML support including prosody, emphasis, breaks |
| Style support | Not available for Finnish voices (no speaking styles for `fi-FI`) |

**Key advantage:** Native mulaw eliminates ffmpeg. `stop_speaking_async()`
gives explicit barge-in cancellation (vs client-side stream close for GCP).

**Key disadvantage:** Higher latency (200-400ms TTFA) degrades
conversational UX. The ~50MB native SDK binary increases container size.
Finnish voice selection is limited to 3 neural voices (vs 30 Chirp 3: HD
styles on GCP and expressive voices on ElevenLabs).

**SDK interaction model:** Azure wraps the WebSocket internally. You
interact with Python objects and event callbacks, not raw connections. Less
control but also less plumbing than ElevenLabs or GCP.

### Azure Speech-to-Text

| Aspect | Detail |
|--------|--------|
| API | Native SDK, push stream + event callbacks |
| Audio input | **Native MULAW 8kHz** via `AudioStreamFormat.get_wave_format_pcm()` with mulaw encoding |
| Push stream | `PushAudioInputStream` — write audio bytes directly, no file/microphone required |
| Partial results | `recognizing` event fires with interim text |
| Final results | `recognized` event fires with committed text |
| VAD | Configurable silence timeout via `SpeechConfig.set_property()`: `SpeechServiceConnection_EndSilenceTimeoutMs` (100-5000ms) |
| Finnish | Fully supported (`fi-FI`) |
| Persistent mode | **`start_continuous_recognition_async()` — no duration limit** |
| Per-turn mode | `recognize_once_async()` — single utterance, auto-stops on silence |
| Auth | Subscription key + region string |
| Pricing | $1/audio hour (standard), $1.40/audio hour (custom) |
| Library | `azure-cognitiveservices-speech` (same SDK as TTS) |
| Word-level timestamps | Supported via `request_word_level_timestamps()` |

**Key advantage over GCP STT:** No streaming duration limit. Continuous
recognition runs for the full call duration without stream rotation. This
eliminates the 5-minute rotation complexity that makes GCP problematic for
persistent mode.

**Key advantage over ElevenLabs STT:** Push stream pattern maps directly
to the inbound queue:
```python
push_stream.write(audio_bytes)  # equivalent to stt.send({"audio_base_64": ...})
```

The SDK event model (`recognizing`/`recognized`) maps cleanly to the
existing `_on_partial`/`_on_committed` pattern in `PersistentSttSession`.

**VAD configuration:** Silence timeout is configurable per-session via
`SpeechServiceConnection_EndSilenceTimeoutMs`, matching the commit trigger
pattern used in the ElevenLabs persistent STT.

## Scope

### Phase 1: Azure PersistentStt (highest production value)

New file: `voice_runtime/providers/azure_stt.py`

```python
class AzurePersistentStt:
    """Azure Speech-to-Text provider for persistent mode.

    Uses continuous recognition with push stream for unlimited-duration
    streaming. Event-based partial/final transcripts map to barge-in
    and commit patterns.
    """

    def __init__(
        self,
        subscription_key: str | None = None,
        region: str = "westeurope",
        language_code: str = "fi-FI",
        silence_timeout_ms: int = 1500,
    ) -> None: ...

    async def start(self, inbound_queue: asyncio.Queue[bytes | None]) -> None:
        """Open push stream, configure recognizer, begin feeding audio."""
        ...

    async def stop(self) -> None:
        """Stop continuous recognition and close push stream."""
        ...

    def set_speaking(self, speaking: bool) -> None:
        """Toggle TTS speaking state for echo discard."""
        ...

    def arm_barge_in(self) -> asyncio.Event:
        """Return event that fires on partial transcript during TTS."""
        ...

    async def next_transcript(self, timeout: float = 30.0) -> str | None:
        """Await next committed transcript from recognized event."""
        ...
```

**Implementation approach:**

1. **Push stream setup:**
   ```python
   stream_format = speechsdk.audio.AudioStreamFormat(
       samples_per_second=8000,
       bits_per_sample=8,
       channels=1,
       wave_stream_format=speechsdk.AudioStreamWaveFormat.MULAW,
   )
   push_stream = speechsdk.audio.PushAudioInputStream(stream_format)
   audio_config = speechsdk.audio.AudioConfig(stream=push_stream)
   ```

2. **Continuous recognition:**
   ```python
   recognizer = speechsdk.SpeechRecognizer(
       speech_config=speech_config,
       audio_config=audio_config,
   )
   recognizer.recognizing.connect(self._on_partial)
   recognizer.recognized.connect(self._on_committed)
   recognizer.start_continuous_recognition_async()
   ```

3. **Audio feed loop** (background task):
   ```python
   while True:
       chunk = await inbound_queue.get()
       if chunk is None:
           push_stream.close()
           break
       push_stream.write(chunk)
   ```

4. **Barge-in:** `_on_partial` checks if `speaking` flag is set and fires
   the barge-in event on meaningful partial text (same logic as ElevenLabs).

5. **Echo discard:** When `set_speaking(True)` is called, partials during
   TTS playback are suppressed. A grace window after `set_speaking(False)`
   filters residual echo (same pattern as ElevenLabs).

Factory extension in `voice_runtime/stt.py`:
```python
elif provider == "azure":
    if mode == "persistent":
        from voice_runtime.providers.azure_stt import AzurePersistentStt
        return AzurePersistentStt(**kwargs)
    elif mode == "per_turn":
        from voice_runtime.providers.azure_stt import AzurePerTurnStt
        return AzurePerTurnStt(**kwargs)
    raise ValueError(f"Unknown Azure STT mode: {mode}")
```

### Phase 2: Azure PerTurnStt (simple addition)

Same file: `voice_runtime/providers/azure_stt.py`

```python
class AzurePerTurnStt:
    """Azure Speech-to-Text provider for per-turn mode.

    Uses recognize_once_async() for single-utterance recognition.
    Auto-stops on silence — simpler than ElevenLabs per-turn.
    """

    def __init__(
        self,
        subscription_key: str | None = None,
        region: str = "westeurope",
        language_code: str = "fi-FI",
    ) -> None: ...

    def listen(self, session: VoiceSession, timeout: float = 30.0) -> str:
        """Feed audio to push stream, return recognized text."""
        ...
```

**Implementation approach:**
- Create `PushAudioInputStream` with mulaw format
- Feed audio from `session.inbound` in a background thread
- Call `recognizer.recognize_once_async().get()` — blocks until utterance
  ends or silence timeout
- Return `result.text` or empty string on no-match/cancellation

### Phase 3: Azure TTS (fallback option)

New file: `voice_runtime/providers/azure_tts.py`

```python
class AzureTTS:
    """Azure Text-to-Speech provider.

    Uses event-based synthesis with native MULAW 8kHz output.
    No ffmpeg needed.
    """

    def __init__(
        self,
        subscription_key: str | None = None,
        region: str = "westeurope",
        voice_name: str = "fi-FI-NooraNeural",
        language_code: str = "fi-FI",
    ) -> None: ...

    def speak(
        self,
        text: str,
        session: VoiceSession,
        stop_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """Synthesize text to mulaw audio and stream to session."""
        ...
```

**Implementation approach:**

1. **Audio config for raw mulaw output:**
   ```python
   speech_config.set_speech_synthesis_output_format(
       speechsdk.SpeechSynthesisOutputFormat.Raw8Khz8BitMonoMULaw
   )
   ```

2. **Event-based streaming:**
   ```python
   synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)
   synthesizer.synthesizing.connect(lambda evt: self._on_audio_chunk(evt, session, stop_event))
   result = synthesizer.speak_text_async(text).get()
   ```

3. **`_on_audio_chunk` callback:**
   - Check `stop_event.is_set()` for barge-in → call `synthesizer.stop_speaking_async()`
   - Check `session.is_disconnected` for call end
   - Chunk audio into 160-byte frames (20ms at 8kHz mulaw)
   - Call `session.put_outbound_sync(chunk)` + `session.tap_agent(chunk)`

4. **Mark sync:** `session.send_mark_and_wait("tts_complete")` after last chunk.

Factory extension in `voice_runtime/tts.py`:
```python
elif provider == "azure":
    from voice_runtime.providers.azure_tts import AzureTTS
    return AzureTTS(**kwargs)
```

## Design Decisions

### Authentication

Azure uses a subscription key + region string. Two configuration approaches:

1. **Environment variables** (preferred):
   - `AZURE_SPEECH_KEY` — subscription key
   - `AZURE_SPEECH_REGION` — region (e.g., `westeurope`, `northeurope`)
2. **Constructor parameters** — override for testing / multi-subscription

The `SpeechConfig` constructor accepts both:
```python
speech_config = speechsdk.SpeechConfig(
    subscription=key,
    region=region,
)
```

### SDK binary size concern

The `azure-cognitiveservices-speech` package includes a ~50MB native C++
binary. This is a meaningful increase to container image size. Mitigations:
- Install as optional dependency only when Azure provider is selected
- Multi-stage Docker build: install in build stage, copy only needed files
- Production images already include ElevenLabs SDK (~20MB) + ffmpeg (~80MB),
  so the Azure SDK is comparable

After NC-159 eliminates ffmpeg (~80MB), the net container size impact of
adding Azure SDK (~50MB) is negative (saves ~30MB).

### No abstract base classes

Per the provider alternatives doc: "The factory function IS the interface."
Azure providers must satisfy the same duck-typed contract — same method
signatures, same return dict shapes — without formal Protocol or ABC.

### Threading model

Azure SDK event callbacks run on SDK-internal threads, not the asyncio
event loop. The persistent STT implementation must bridge:
- SDK thread → `asyncio.Event` / `asyncio.Queue` via `loop.call_soon_threadsafe()`
- This is the same pattern used by ElevenLabs `PersistentSttSession`
  (`_on_partial` and `_on_committed` bridge to async via thread-safe calls)

### Dependencies

```
azure-cognitiveservices-speech >= 1.41.0
```

Optional dependency — add to `pyproject.toml` extras:
```toml
[project.optional-dependencies]
azure = ["azure-cognitiveservices-speech>=1.41.0"]
```

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `AZURE_SPEECH_KEY` | Azure Speech subscription key |
| `AZURE_SPEECH_REGION` | Azure Speech region (default: `westeurope`) |
| `AZURE_STT_LANGUAGE` | Override STT language code (default: `fi-FI`) |
| `AZURE_TTS_VOICE` | Override TTS voice name (default: `fi-FI-NooraNeural`) |
| `AZURE_TTS_LANGUAGE` | Override TTS language code (default: `fi-FI`) |

### Prerequisite

**NC-159 (ElevenLabs native mulaw) should be completed first.** It simplifies
the ElevenLabs baseline and the ffmpeg elimination makes the Docker image
size comparison more favorable for adding the Azure SDK.

### Relationship to NC-160 (GCP)

NC-160 and NC-161 are complementary, not competing:

| Capability | Best provider | Rationale |
|-----------|--------------|-----------|
| Per-turn STT | **GCP** (NC-160) | `single_utterance=true` is simpler than Azure's `recognize_once_async` |
| Persistent STT | **Azure** (NC-161) | No duration limit vs GCP's 5-min rotation |
| TTS (primary) | **ElevenLabs** | Best voice quality + lowest latency |
| TTS (fallback) | **GCP or Azure** | GCP has more Finnish voices; Azure has richer SSML |

Implementation order per the provider alternatives doc:
1. NC-159 — eliminate ffmpeg (immediate, no new provider)
2. NC-160 Phase 1 — GCP PerTurnStt (simplest, validates factory pattern)
3. **NC-161 Phase 1 — Azure PersistentStt** (highest production value)
4. TTS providers — only if ElevenLabs needs replacement

## Acceptance Criteria

### Phase 1: Azure PersistentStt
- [ ] `voice_runtime/providers/azure_stt.py` exists with `AzurePersistentStt` class
- [ ] `start()` opens push stream and begins continuous recognition
- [ ] `stop()` stops recognition and closes push stream cleanly
- [ ] `next_transcript()` returns committed text from `recognized` event
- [ ] `set_speaking()` toggles echo discard (suppress partials during TTS)
- [ ] `arm_barge_in()` returns `asyncio.Event` firing on meaningful partial
- [ ] Push stream accepts raw MULAW 8kHz audio from inbound queue
- [ ] No streaming duration limit — validated with > 5 min test
- [ ] `create_stt(provider="azure", mode="persistent")` returns `AzurePersistentStt`
- [ ] Unit tests with mocked SDK (no real API calls in unit tests)
- [ ] Integration test (requires `AZURE_SPEECH_KEY` + `AZURE_SPEECH_REGION`, skipped without)
- [ ] Finnish (`fi-FI`) used as default language

### Phase 2: Azure PerTurnStt
- [ ] `AzurePerTurnStt` class in same file
- [ ] `listen()` returns transcript string using `recognize_once_async()`
- [ ] `create_stt(provider="azure", mode="per_turn")` returns `AzurePerTurnStt`
- [ ] Auto-stops on utterance end (silence timeout)
- [ ] Unit tests with mocked SDK

### Phase 3: Azure TTS
- [ ] `voice_runtime/providers/azure_tts.py` exists with `AzureTTS` class
- [ ] `speak()` returns `{"last_spoken": text}` dict
- [ ] Audio output is `Raw8Khz8BitMonoMULaw` — no ffmpeg
- [ ] `synthesizing` event streams audio chunks to session
- [ ] Barge-in via `stop_event` → `stop_speaking_async()`
- [ ] Mark sync via `session.send_mark_and_wait("tts_complete")`
- [ ] `create_tts(provider="azure")` returns `AzureTTS`
- [ ] Unit tests with mocked SDK
- [ ] Integration test (skipped without credentials)

## Effort Estimate

| Component | Effort | Notes |
|-----------|--------|-------|
| `providers/azure_stt.py` (persistent) | 0.75 day | Push stream + continuous recognition + echo discard |
| `providers/azure_stt.py` (per-turn) | 0.25 day | `recognize_once_async()` is trivial |
| `providers/azure_tts.py` | 0.5 day | Event-based, no ffmpeg |
| Factory `elif` additions | 10 min | Two lines each in `stt.py` and `tts.py` |
| Unit tests (all providers) | 0.5 day | Mock SDK classes |
| Integration tests | 0.25 day | Requires Azure subscription |
| **Total** | **~2.25 days** | |

## What NOT to build

- **Custom voice models** — Azure supports professional voice fine-tuning
  for Finnish, but this requires recorded training data and Azure portal
  configuration. Out of scope.
- **SSML generation** — Finnish voices don't support speaking styles. Plain
  text input is sufficient. SSML can be added incrementally if prosody
  control is needed later.
- **Word-level timestamps** — Azure supports them but voice_runtime doesn't
  use them. Don't add unused capabilities.
- **Abstract provider base class** — validate interface through duck typing
  with the second and third providers. Formalize only if patterns diverge.
- **Azure AD token auth** — subscription key is simpler and sufficient for
  server-side services. AD token adds OAuth2 flow complexity without benefit
  for a single-service deployment.

## Judgement

**Verdict: APPROVED** — 2026-03-19

### Verification Results

All 12 Azure SDK API claims verified against `azure-cognitiveservices-speech==1.48.2`:
- `AudioStreamWaveFormat.MULAW` ✅
- `SpeechSynthesisOutputFormat.Raw8Khz8BitMonoMULaw` ✅
- `PushAudioInputStream` ✅
- `start_continuous_recognition_async` / `stop_continuous_recognition_async` ✅
- `recognizing` / `recognized` events ✅
- `speak_text_async` / `stop_speaking_async` ✅
- `synthesizing` event ✅
- `recognize_once_async` ✅
- `AudioStreamFormat` constructor with `wave_stream_format=MULAW` ✅
- `SpeechServiceConnection_EndSilenceTimeoutMs` in `PropertyId` ✅
- `PushAudioInputStream(fmt)` → `AudioConfig(stream=stream)` pipeline ✅

Existing codebase alignment verified:
- `create_stt()` factory: `elif provider == "azure"` addition straightforward (29 lines)
- `create_tts()` factory: same pattern (18 lines)
- `PersistentSttSession` interface: all 5 public methods (`start`, `stop`, `next_transcript`, `set_speaking`, `arm_barge_in`) matched exactly in FR proposal
- `PerTurnStt.listen()` interface: matched (FR uses `VoiceSession` type vs existing `Any` — acceptable improvement)
- `ElevenLabsTTS.speak()` return dict contract: matched exactly (4 return shapes)
- NC-159 prerequisite: completed ✅

### Amendments

1. **Research table STT typo** — Table mentions `AudioStreamFormat.get_wave_format_pcm()` with mulaw encoding, but `get_wave_format_pcm` does not exist on the class. The implementation code section correctly uses `AudioStreamFormat(wave_stream_format=AudioStreamWaveFormat.MULAW)`, so the implementation plan is sound. The table is cosmetically wrong — no behavioral impact.

2. **No reconnect needed for Azure** — ElevenLabs `PersistentSttSession.set_speaking(False)` reconnects Scribe WebSocket when TTS lasted >10s (degradation recovery). Azure continuous recognition has no duration limit and no WebSocket degradation pattern. The Azure implementation must NOT include reconnect logic — this is an ElevenLabs-specific workaround, not a generic pattern. Explicitly exclude from implementation scope.

3. **Echo discard grace window** — FR says "same pattern as ElevenLabs" but doesn't specify the 0.4s `_discard_until` value. Use the same 0.4s value initially — it can be tuned per-provider later if Azure's echo characteristics differ.

4. **Phase independence** — FR says NC-160 (GCP per-turn) should come first to validate the factory pattern. However, NC-161 Phase 1 (Azure persistent STT) is independently valuable and the factory pattern is trivial (one `elif` branch). If NC-160 is delayed, Phase 1 can proceed standalone. The factory extension is two lines — no validation needed beyond tests.

5. **SDK version pin** — FR specifies `>=1.41.0`. Current installed version is `1.48.2`. The SDK has been stable across minor versions. The pin is reasonable. Consider adding upper bound `<2.0` to protect against major breaking changes.

6. **Test count** — voice_runtime currently has 99 tests (26 classes, ~95 methods). FR doesn't estimate new test count. Expect ~15-20 new tests for Phase 1 (persistent STT): constructor, start/stop lifecycle, push stream feeding, partial/committed callbacks, echo discard, barge-in, next_transcript timeout, factory dispatch, error handling. Phase 2 adds ~5 (per-turn listen, timeout, disconnect, factory). Phase 3 adds ~8 (speak, barge-in, mark sync, factory). Total: ~30 new tests across all phases.
