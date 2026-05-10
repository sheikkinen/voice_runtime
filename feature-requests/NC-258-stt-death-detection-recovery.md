# NC-258: STT Death Detection and Recovery

**Status:** Judged
**Priority:** Critical
**Type:** Bug fix / Enhancement
**Affects:** voice_runtime, ninchat_voice

## Problem

When Azure STT dies mid-call (network drop, auth expiry, service error), **nothing detects or reports it**. The caller hears silence until `pre_speech_silence_s` (300s = 5 minutes) expires.

### Evidence

2026-04-28: Three consecutive calls (07:20, 07:22, 07:24) after a Fly machine restart had zero user transcriptions. The working call (07:18) had full multi-turn Finnish conversation. STT provider: Azure (`STT_PROVIDER=azure`). Zero STT-related entries in any log — the failure was completely invisible.

### Root Cause

1. **Azure STT provider has no error handler.** The Azure SDK provides a `recognizer.canceled` signal that fires on connection loss, auth failure, or service timeout. `AzurePersistentStt` never connects to it. (ElevenLabs has `_on_error` + `_reconnect_after_error`; Azure has nothing.)

2. **No STT lifecycle event reaches the FSM.** The `SttProvider` protocol defines only `on_committed` and `on_recognizing` callbacks. There is no `on_error` or `on_restarted` callback. When STT dies silently, the FSM sits in `graph_listening` with no events arriving.

3. **FSM has no STT health watchdog.** The silence detector's `pre_speech_silence_s: 300` is the only safety net — 5 minutes of dead air before the call closes.

## Objective

Detect Azure STT death, attempt auto-reconnect, and notify the FSM so it can take immediate action (re-enter listening state or close gracefully).

## Design

### Layer 1: Provider boundary (`voice_runtime`)

#### 1a. `SttProvider` protocol — add `on_error` callback

```python
class SttProvider(Protocol):
    on_committed: Callable[[str], None] | None
    on_recognizing: Callable[[str], None] | None
    on_error: Callable[[str], None] | None          # NC-258: STT lifecycle error

    def set_speaking(self, speaking: bool) -> None: ...
    async def start(self, inbound_queue: asyncio.Queue[bytes | None]) -> None: ...
    async def stop(self) -> None: ...
```

`on_error` fires with a reason string when STT encounters a fatal error. The provider MUST attempt reconnect internally before firing `on_error` — the callback signals "reconnect failed or session permanently degraded."

**J-1: Max retry bound.** Providers MUST cap reconnect attempts at `_MAX_RECONNECT_ATTEMPTS = 3`. Fire `on_error` after exhausting retries. This bounds dead-air to ~7s of backoff (1+2+4) before the FSM receives the error event.

#### 1b. `AzurePersistentStt` — connect `canceled` signal + reconnect

```python
# In start():
self._recognizer.canceled.connect(self._on_canceled)

# New handler:
def _on_canceled(self, evt: Any) -> None:
    reason = evt.cancellation_details.reason
    error_details = evt.cancellation_details.error_details

    if reason == speechsdk.CancellationReason.Error:
        logger.error("Azure STT canceled: %s — %s", reason, error_details)
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._reconnect_after_error(), self._loop)
    elif reason == speechsdk.CancellationReason.EndOfStream:
        logger.info("Azure STT: end of stream (expected)")
    else:
        logger.warning("Azure STT canceled: %s", reason)
```

Add `_reconnect_after_error` method (mirror ElevenLabs pattern with exponential backoff + jitter):

```python
_RECONNECT_BASE_DELAY_S = 1.0
_RECONNECT_MAX_DELAY_S = 30.0
_MAX_RECONNECT_ATTEMPTS = 3          # J-1: bound dead-air window

async def _reconnect_after_error(self) -> None:
    if self._reconnect_attempt >= self._MAX_RECONNECT_ATTEMPTS:
        logger.error("Azure STT reconnect exhausted (%d attempts)",
                     self._reconnect_attempt)
        if self.on_error:
            self.on_error(f"reconnect_exhausted_after_{self._reconnect_attempt}_attempts")
        return

    delay = min(self._RECONNECT_BASE_DELAY_S * (2 ** self._reconnect_attempt),
                self._RECONNECT_MAX_DELAY_S)
    delay *= 0.75 + random.random() * 0.5
    logger.info("Reconnecting Azure STT in %.1fs (attempt %d/%d)...",
                delay, self._reconnect_attempt + 1, self._MAX_RECONNECT_ATTEMPTS)
    await asyncio.sleep(delay)
    try:
        await self._reconnect()
        self._reconnect_attempt = 0
    except Exception as exc:
        self._reconnect_attempt += 1
        logger.error("Azure STT reconnect failed (attempt %d): %s",
                     self._reconnect_attempt, exc)
        await self._reconnect_after_error()  # recurse with incremented counter
```

**J-2: `_reconnect()` definition.** Azure reconnect differs structurally from ElevenLabs:

```python
async def _reconnect(self) -> None:
    """Tear down and re-create recognizer + push stream."""
    # 1. Stop old recognizer
    if self._recognizer:
        with contextlib.suppress(Exception):
            self._recognizer.stop_continuous_recognition_async().get()
    # 2. Close old push stream
    if self._push_stream:
        with contextlib.suppress(Exception):
            self._push_stream.close()
    # 3. Re-create push stream + recognizer (same config)
    stream_format = speechsdk.audio.AudioStreamFormat(...)
    self._push_stream = speechsdk.audio.PushAudioInputStream(stream_format)
    audio_config = speechsdk.audio.AudioConfig(stream=self._push_stream)
    speech_config = speechsdk.SpeechConfig(...)
    self._recognizer = speechsdk.SpeechRecognizer(...)
    self._recognizer.recognized.connect(self._on_committed)
    self._recognizer.recognizing.connect(self._on_recognizing)
    self._recognizer.canceled.connect(self._on_canceled)
    self._recognizer.start_continuous_recognition_async().get()
```

Do NOT restart `_feed_task` — it keeps reading from the same inbound queue. Only the push stream sink changes.

**J-5: `_stopping` guard.** Add `self._stopping = False`. Set in `stop()` before teardown. Guard `_on_canceled`: if `self._stopping`, log and return without reconnecting. Prevents reconnect during normal call teardown.

#### 1c. `AzurePersistentStt._feed_audio` — add frame count logging

Mirror ElevenLabs pattern: log every 100 frames.

```python
frame_count += 1
if frame_count % 100 == 0:
    logger.info("_feed_audio: %d frames fed (speaking=%s)", frame_count, self._speaking)
```

#### 1d. `ElevenLabs PersistentSttSession` — add `on_error` field

Add `self.on_error: Callable[[str], None] | None = None` for protocol conformance. Fire it when reconnect fails:

```python
# In _reconnect_after_error, after max retries:
if self.on_error:
    self.on_error(f"reconnect_failed: {exc}")
```

**J-1: Apply same `_MAX_RECONNECT_ATTEMPTS = 3` bound to ElevenLabs** for consistency.

**J-6: Fire `on_error` from `_on_time_limit`.** Currently `_on_time_limit` sets `_time_limit_event` (feed loop exits) but never notifies the consumer. The FSM has no way to know STT is permanently degraded. Add:

```python
def _on_time_limit(self, data: dict) -> None:
    logger.critical("ElevenLabs session time limit exceeded — STT degraded. data=%s", data)
    if self._loop:
        self._loop.call_soon_threadsafe(self._time_limit_event.set)
    if self.on_error:
        self.on_error("session_time_limit_exceeded")
```

### Layer 2: Consumer boundary (`ninchat_voice`)

#### 2a. `SttConsumer` — add `on_error` method

```python
def on_error(self, reason: str) -> None:
    """Forward STT error to FSM as stt_error event."""
    if self._event_sender is not None:
        try:
            self._event_sender.send_event("stt_error", {"reason": reason})
            logger.error("STT error → stt_error: %s", reason)
        except Exception as exc:
            logger.warning("STT error dispatch failed: %s", exc)
```

#### 2b. Bridge handler wiring — wire `on_error`

In `_on_speak` (bridge_handlers.py), alongside the existing `on_committed` / `on_recognizing` wiring:

```python
session.stt.on_error = consumer.on_error
```

### Layer 3: FSM boundary (`config/voice_coordinator_*.yaml`)

#### 3a. Event definition

```yaml
events:
  stt_error:
    context_map:
      stt_error_reason: payload.reason
```

#### 3b. Transition: `graph_listening` → `speaking_error` on `stt_error`

```yaml
- { from: graph_listening, to: speaking_error, event: stt_error }
```

This reuses NC-257's `speaking_error` state — plays the apology message ("Pahoittelut, Auto Matti on epäkunnossa. Kuulemiin.") and closes the call gracefully. The caller gets immediate feedback instead of 5 minutes of silence.

#### 3c. Additional transitions (mandatory, J-4)

STT can die in any state. Without these, `stt_error` arriving during `graph_processing` or `graph_speaking` is silently dropped — the FSM never recovers.

```yaml
- { from: graph_speaking, to: speaking_error, event: stt_error }
- { from: graph_processing, to: speaking_error, event: stt_error }
- { from: ack_speaking, to: speaking_error, event: stt_error }
```

Alternative: single wildcard `from: "*", to: speaking_error, event: stt_error` — simpler, and `speaking_error` already handles all exits gracefully. Preferred if no state needs to ignore `stt_error`.

## Scope

### In scope
- Azure `canceled` signal handler + reconnect with backoff
- `on_error` callback in `SttProvider` protocol
- `on_error` in both Azure and ElevenLabs providers
- `SttConsumer.on_error` → `stt_error` FSM event
- Bridge wiring of `on_error`
- FSM `stt_error` event + transitions in **navigator** config only (active mode)
- Frame count logging in Azure `_feed_audio`
- `SttTee` proxy for `on_error` (J-3)
- `_stopping` guard against teardown race (J-5)
- ElevenLabs `_on_time_limit` → `on_error` (J-6)
- Max retry bound on both providers (J-1)

### Out of scope (future FRs)
- FSM-level STT liveness watchdog (silence_detector enhancement)
- Automatic STT provider failover (Azure → ElevenLabs)
- Other coordinator modes (triage, bargein, simple, questionnaire) — add after navigator proves the pattern

## Acceptance Criteria

1. Azure STT `canceled` signal is connected and logs reason + error_details
2. On cancellation with `CancellationReason.Error`, provider attempts reconnect with exponential backoff
3. If reconnect fails, `on_error` callback fires
4. `SttConsumer` forwards error as `stt_error` FSM event
5. FSM transitions from `graph_listening` to `speaking_error` on `stt_error`
6. Caller hears apology message and call closes gracefully
7. Azure `_feed_audio` logs frame counts every 100 frames
8. Unit tests cover: canceled handler, reconnect success, reconnect failure → on_error, FSM transition
9. `SttTee` proxies `on_error` to primary provider (J-3)
10. Reconnect does not fire during normal `stop()` teardown (J-5)
11. ElevenLabs `_on_time_limit` fires `on_error("session_time_limit_exceeded")` (J-6)
12. Both providers cap reconnect at 3 attempts before firing `on_error` (J-1)

## Files Changed

| File | Change |
|------|--------|
| `voice_runtime/providers/__init__.py` | Add `on_error` to `SttProvider` protocol |
| `voice_runtime/providers/azure_stt.py` | `canceled` handler, reconnect, `on_error`, frame logging |
| `voice_runtime/providers/elevenlabs_stt.py` | Add `on_error` field, fire on reconnect failure, `_on_time_limit` → `on_error`, max retry cap |
| `voice_runtime/stt_tee.py` | Add `on_error` property proxy to primary (J-3) |
| `ninchat_voice/services/stt.py` | Add `on_error()` method to `SttConsumer` |
| `ninchat_voice/services/bridge_handlers.py` | Wire `session.stt.on_error = consumer.on_error` |
| `ninchat_voice/config/voice_coordinator_navigator.yaml` | Add `stt_error` event + transitions |
