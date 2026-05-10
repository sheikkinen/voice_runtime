# NC-199: Listen-Path Utterance Continuation via Recognizing Signal

**Priority:** HIGH
**Type:** Bug
**Status:** Judged — Approved for enforcement (amendments incorporated)
**Effort:** 0.5-1 day
**Requested:** 2026-04-07

## Summary

Extend `_next_stable_transcript` to hold the listen window open when interim/partial recognition signals indicate the user is still speaking. This prevents split-utterance commits from prematurely closing the listen path during long monologues.

## Value Statement

Long user turns (30s+ elderly-care intake monologues) are captured as a single `transcribed` event instead of being split into a listen-path commit plus a lost between-turn commit. No new FSM event required.

## Problem

From the 2026-04-07 elderly-care call log:

| Time | Event |
|------|-------|
| 09:41:11 | TTS ends, `listen()` starts, `_listening=True` |
| 09:41:12–42 | User speaks continuously for ~30s |
| 09:41:42 | Azure VAD commits segment 1 (488 bytes) → `listen()` returns, `_listening=False` |
| 09:41:44 | Azure commits segment 2 (127 bytes) → enters between-turns path |
| ~09:41:45 | NC-198 debounce dispatch fires → `transcribed` sent while FSM already in `graph_processing` → **text lost** |

Root cause: `_next_stable_transcript` returns immediately for non-premature transcripts (long text passes `_looks_premature_transcript`). The Azure `EndSilenceTimeoutMs=1500` triggered mid-utterance during a natural breath pause. The user resumed speaking, but `listen()` had already closed.

NC-198 correctly solved the between-turns duplicate problem. NC-199 solves the upstream issue: `listen()` closes too early during long utterances.

## Available Provider Signals

Both active providers expose interim/partial recognition:

| Signal | Azure SDK | ElevenLabs Scribe |
|--------|-----------|-------------------|
| Interim transcript | `recognizing` event | `partial_transcript` event |
| Final commit | `recognized` event | `committed_transcript` event |
| Speech start | `speech_start_detected` | Not available |
| Speech end | `speech_end_detected` | Not available |

Currently only the commit events are wired. The interim events are ignored.

## Proposed Solution

### A1. Add `on_recognizing` callback to provider protocol

In both `AzurePersistentStt` and `PersistentSttSession` (ElevenLabs):
- Wire the interim event (`recognizing` / `partial_transcript`).
- Fire a new `on_recognizing` callback with the partial text.
- Delivery path clarification:
   - When `session.stt` is `SttTee`, proxy `on_recognizing` to primary only (same as `on_committed`).
   - When `session.stt` is a direct provider instance, wire `on_recognizing` directly in `bridge_handlers`.

### A2. Track "last recognizing" timestamp in SttConsumer

```python
class SttConsumer:
    def __init__(self):
        ...
        self._last_recognizing_ts: float = 0.0

    def on_recognizing(self, text: str) -> None:
        """Called on interim/partial transcript — signals active speech."""
        self._last_recognizing_ts = time.monotonic()
```

### A3. Post-commit continuation grace in `_next_stable_transcript` (looped)

After receiving a committed transcript from `next_transcript()`:

```python
accumulated = first
segments = 1
while (
   segments < STT_MAX_CONTINUATION_SEGMENTS
   and consumer.is_speech_active(grace=STT_CONTINUATION_GRACE_S)
):
   follow_up = await consumer.next_transcript(timeout=STT_CONTINUATION_TIMEOUT_S)
   if not follow_up:
      break
   accumulated = accumulated + " " + follow_up
   segments += 1
return accumulated
```

Where `is_speech_active(grace)` checks:
```python
def is_speech_active(self, grace: float = 2.0) -> bool:
    return (time.monotonic() - self._last_recognizing_ts) < grace
```

### A4. Configurable constants

```
STT_CONTINUATION_GRACE_S = float(os.getenv("STT_CONTINUATION_GRACE_S", "2.0"))
STT_CONTINUATION_TIMEOUT_S = float(os.getenv("STT_CONTINUATION_TIMEOUT_S", "2.5"))
STT_MAX_CONTINUATION_SEGMENTS = int(os.getenv("STT_MAX_CONTINUATION_SEGMENTS", "5"))
```

The grace should be slightly longer than the provider's silence threshold (Azure: 1.5s, ElevenLabs: 1.5s) to allow for the natural commit latency.

### A6. Judgement amendments incorporated

- Loop continuation is mandatory (not a single follow-up), capped by `STT_MAX_CONTINUATION_SEGMENTS`.
- `on_recognizing` wiring is explicit for both runtime shapes:
   - `session.stt` as `SttTee` proxy path
   - direct provider instance path via `bridge_handlers`

### A5. Merge strategy

If a continuation is captured, concatenate with space separator. The provider already handles sentence-level punctuation in each commit.

## Scope

In scope:
- `projects/voice_runtime/providers/azure_stt.py` — wire `recognizing` event.
- `projects/voice_runtime/providers/elevenlabs_stt.py` — wire `partial_transcript` event.
- `projects/voice_runtime/stt_tee.py` — proxy `on_recognizing` to primary.
- `projects/ninchat_voice/services/stt.py` — add `on_recognizing`, `is_speech_active`, continuation logic.
- `projects/ninchat_voice/services/bridge_handlers.py` — wire `on_recognizing` callback.
- Tests in `projects/ninchat_voice/tests/`.

Out of scope:
- FSM changes (no new event type).
- `voice_runtime` protocol changes beyond adding `on_recognizing`.

## Acceptance Criteria

- [ ] Azure `recognizing` event fires `on_recognizing` callback.
- [ ] ElevenLabs `partial_transcript` event fires `on_recognizing` callback.
- [ ] `SttTee` proxies `on_recognizing` to primary.
- [ ] When `is_speech_active()` is true after first commit, `listen()` waits for continuation.
- [ ] Two Azure commits from a long utterance produce one merged transcript.
- [ ] Short clean answers ("Kyllä.", "Okei.") return immediately (no recognizing activity after commit).
- [ ] Existing `_looks_premature_transcript` grace still works.
- [ ] NC-198 between-turns debounce still works for commits that arrive after listen closes.

## Test Plan (TDD)

### Unit

1. `test_continuation_when_recognizing_active`
   - Set `_last_recognizing_ts` to recent, fire commit A, then commit B.
   - Assert merged text returned.

2. `test_no_continuation_when_recognizing_stale`
   - Set `_last_recognizing_ts` to >2s ago, fire one commit.
   - Assert immediate return (no wait).

3. `test_continuation_timeout_returns_accumulated`
   - Set `_last_recognizing_ts` to recent, fire commit A, no follow-up.
   - Assert returns accumulated transcript so far after continuation timeout.

4. `test_multi_segment_loop_stops_on_cap`
   - Keep recognizing active and provide >5 follow-up commits.
   - Assert returned transcript includes at most `STT_MAX_CONTINUATION_SEGMENTS` segments.

5. `test_on_recognizing_updates_timestamp`
   - Fire `on_recognizing("partial text")`.
   - Assert `_last_recognizing_ts` updated.

6. `test_premature_grace_still_works`
   - Existing premature transcript tests unchanged.

### Integration-lite

- Replay the elderly-care split-utterance pattern with mocked Azure events: `recognizing` → `recognized` → `recognizing` → `recognized`. Assert single merged output.

## Alternatives Considered

1. **Length-based heuristic** — hold grace when committed text is long.
   Rejected: arbitrary threshold; doesn't reflect actual speech state.

2. **Increase `EndSilenceTimeoutMs`** from 1500 to 2500.
   Rejected: delays all turn detection including short answers.

3. **FSM `continued-speaking` event** (NC-197).
   Rejected: over-scoped; recognizing signal solves it at the consumer layer.

4. **Always wait for follow-up after first commit.**
   Rejected: adds latency to every turn including quick yes/no answers.

## Risks and Mitigations

- Risk: Continuation grace adds up to 2s latency on split turns.
  Mitigation: Only triggered when recognizing signal is active; short answers have no recent recognizing → no delay.

- Risk: Provider-specific interim event semantics differ.
  Mitigation: Both providers tested; `on_recognizing` is a simple timestamp update — semantic differences in partial text don't matter.

## Related

- `projects/ninchat_voice/feature-requests/NC-198-between-turns-debounce-transcribed-gate.md` (complementary)
- `projects/ninchat_voice/feature-requests/NC-197-stt-segment-completion-contract.md` (rejected alternative)
- `projects/ninchat_voice/docs/2026-04-07-elderlycare-transcribed-timeline.md`
