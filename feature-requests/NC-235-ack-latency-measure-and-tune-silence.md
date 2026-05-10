# NC-235: Instrument Ack Perceptual Latency + Tune Silence Thresholds

**Status:** Draft
**Priority:** Medium (~0.4–0.7s/turn clawback, requires measurement first)
**Difficulty:** Easy — two observation points + config tune
**Area:** `services/tts.py`, `services/stt.py`, `actions/real/voice_speak_action.py` (or bridge hook), `services/metrics.py` (wire existing histograms), `config/voice_coordinator_navigator.yaml`
**Depends on:** NC-232 (silence 1.2s baseline, `NV_TURN_WALL_SECONDS` registered, precanned mulaw manifest)
**Unblocks:** NC-233 prefetch (once real tail statistics are known)

## Problem

Call `CA16fd5a...` (5 turns, 2026-04-20 15:28:40) was reconstructed from transcript + coordinator logs. User reported "~3s delay to ack"; measurements show **perceptual ~1.5–1.7s**:

| Component (T2–T5) | Wall-clock | Share |
|---|---:|---:|
| `speech_silence_s` (silence gate) | 1.20s | **~75%** |
| FSM dispatch (speech_complete → bridge.send) | ~0.18s | ~11% |
| manifest lookup + first mulaw chunk on wire | <0.01s | <1% |
| Twilio jitter/audio buffer (estimated) | ~0.20s | ~13% |
| **Perceptual total** | **~1.6s** | |

Ack is **precanned** — `audio/ack_processing.mulaw` is streamed directly to Twilio via `_speak_from_file()`; ElevenLabs is not invoked. There is no TTS TTFB in the ack path. My earlier analysis wrongly attributed 0.4–0.7s to TTS TTFB; that estimate only applies to probes/recap/farewell, not ack.

Two problems follow from this correction:

1. **No ground-truth metric for perceived latency.** `NV_TURN_WALL_SECONDS` was registered in NC-232 but is never observed. The 1.6s figure above is derived from log timestamps on a single call — we cannot see tail behaviour or cross-call variance without wiring. NC-233's go/no-go gate depends on exactly this number.
2. **The silence gate dominates perceptual latency** (75%) and has never been tuned below 1.2s. With ack being zero-latency mulaw, the silence floor is now the only meaningful lever left before prefetching LLM work.

## Proposal

Two sequential zero-risk changes. Measure first, then tune. Both under the same FR because the tune is only justified if the measurement shows the silence gate is actually the dominant term in production (as our single-call analysis suggests), and the measurement is worthless without a subsequent tune-or-keep decision.

### 1. Wire `NV_TURN_WALL_SECONDS` observation

Record the perceptual latency from the FSM's perspective: **`speech_complete` timestamp → first outbound mulaw chunk written to the session**.

Boundary normalization: this is the earliest timestamp the FSM can observe ("user finished per VAD") to the latest timestamp before the telco transport ("first byte leaving our process"). Everything before `speech_complete` (microphone → Twilio → Azure → our STT) is outside our control; everything after `put_outbound_sync` is network.

Touch points (minimal; exactly one write per turn):

- **Capture `t_speech_complete`**: when the silence_detector emits `speech_complete`. The coordinator state machine already logs this; add a context write in the action handler that fires `speech_complete` so the value is available to downstream action handlers (Unix-time float, stored in the bridge's per-call context dict).
- **Observe first outbound byte**:
  - In `services/tts.py::_speak_from_file()`: on the **first** `session.put_outbound_sync()` call of a turn, read `t_speech_complete` from context and call `NV_TURN_WALL_SECONDS.observe(time.time() - t_speech_complete)`. Guard with `if t_speech_complete is not None` and clear it after observation so the next turn's TTS (probe) does not also observe against the stale value.
  - Same instrumentation in the ElevenLabs path (`voice_runtime.tts.elevenlabs._on_first_chunk` or equivalent) so probe/recap latency is captured too. Use an optional label `path={"precanned","tts"}` on `NV_TURN_WALL_SECONDS` only if necessary; prefer to keep the histogram unlabeled and rely on co-located `nv_stt_silence_seconds` for breakdown.
- **Observe `NV_STT_SILENCE_SECONDS`**: already registered but unused. The silence_detector action knows the `silence_s` it waited for (0.8s adaptive or 1.2s full). Observe this value on every `speech_complete` emission.

No new metrics are added. Two existing histograms are wired.

### 2. Lower silence thresholds (deferred: only ship after measurement)

Config change in `config/voice_coordinator_navigator.yaml`:

```yaml
- type: silence_detector
  repeatable: true
  params:
    speech_silence_s: 0.8        # was 1.2 (NC-232)
    min_speech_silence_s: 0.5    # was 0.8 (NC-232)
    adaptive: true
    pre_speech_silence_s: 300
    speech_complete_event: speech_complete
    silence_timeout_event: silence_timeout
```

**Why 0.8 / 0.5:**
- 0.8s full silence is the lower bound of the turn-taking literature's transition relevance place for Finnish conversational dialogue (Stivers et al. 2009 cross-linguistic study; Finnish mean gap ~236ms, 95th percentile ~750ms for cooperative turn exchanges).
- 0.5s adaptive floor matches the micropause threshold — below this is a breath or clause boundary, not a completed turn. Anything lower risks cutting a listed enumeration ("Kyllä… ja lisäksi…").
- Expected perceptual latency reduction: **T2–T5 from ~1.6s → ~1.2s**; short utterances (T1) from ~1.0s → ~0.7s.

**Ship condition:** only after Step 1 measurement on ≥10 calls confirms:
- p95 `nv_turn_wall_seconds` ≥ 1.4s (i.e. silence gate really is dominant)
- p95 `nv_stt_silence_seconds` clusters near the current 1.2s cap (not distributed; a distributed histogram means users already produce the full silence and dropping the cap will cut valid speech)

If either condition fails, do **not** drop the thresholds; close this FR's Step 2 as "measured, no change warranted" and document in diary.

## Non-Goals

- Not changing ack audio content (user explicitly noted ack is precanned; swapping `ack_processing` → `kiitos` is a separate decision about ack *duration*, not ack *latency*).
- Not touching ElevenLabs TTS TTFB (probe/recap path, out of scope here).
- Not implementing NC-233 prefetch. This FR produces the statistics NC-233 needs as input.
- Not adding per-path labels unless the unlabeled histogram proves ambiguous.

## Design Rationale

**Why measure before tune.** The Scripture's boundary rule applies: latency numbers enter our system at two boundaries (VAD's silence decision, and telco's first outbound byte). Every downstream estimate is derived. Without observation at both boundaries, we cannot tell whether a tune actually moved the dial or whether Twilio's jitter budget changed that week. NC-232's deferred baseline capture is the same gate moved earlier.

**Why not split into two FRs.** Step 2 is a trivial config change. Splitting would require two Judgements, two enforcement cycles, and two diary entries for what is operationally one decision: "is the silence gate the thing to tune next, or isn't it?"

**Why reuse existing histograms.** `NV_TURN_WALL_SECONDS` and `NV_STT_SILENCE_SECONDS` were both registered for this purpose. Adding `nv_ack_perceptual_*` or `nv_first_byte_*` would be duplicate cardinality for the same observation.

## Acceptance Criteria

All commands assume `cd projects/ninchat_voice && source .venv/bin/activate`.

### Step 1: Observation wiring

- [ ] `grep -rn 'NV_TURN_WALL_SECONDS.observe' services/ actions/` returns ≥1 match.
- [ ] `grep -rn 'NV_STT_SILENCE_SECONDS.observe' services/ actions/` returns ≥1 match.
- [ ] A unit test under `tests/` exercises `_speak_from_file` with a stubbed `t_speech_complete` in context and asserts `NV_TURN_WALL_SECONDS` was observed exactly once. File name suggestion: `tests/test_nc235_turn_wall_observation.py`.
- [ ] A unit test exercises the silence_detector callback path and asserts `NV_STT_SILENCE_SECONDS` is observed with the configured `silence_s` value.
- [ ] After an end-to-end smoke call with metrics exposed:
      ```bash
      curl -s http://localhost:9091/metrics | \
        grep -E '^(nv_turn_wall_seconds|nv_stt_silence_seconds)_(count|sum|bucket)'
      ```
      returns non-zero `_count` on both histograms.
- [ ] Context key used to pass `t_speech_complete` is documented as a comment at its write site (which action or handler sets it).
- [ ] `t_speech_complete` is cleared from context after being observed, so a second TTS in the same turn (e.g. ack then probe) does not re-observe against a stale value.

### Step 2: Measurement gate

- [ ] Run **≥10 real calls** with Step 1 deployed. Record:
  - p50, p95, max of `nv_turn_wall_seconds`
  - p50, p95, distribution of `nv_stt_silence_seconds` (is the histogram concentrated at 1.2s or spread?)
  - count of `speech_complete` events where `nv_stt_silence_seconds` < 0.9 (adaptive path triggered)
- [ ] Numbers recorded in diary entry at `docs/diary/YYYY-MM-DD-nc-235-measurement.md`.
- [ ] Diary entry concludes: **`SHIP_STEP_2: yes`** or **`SHIP_STEP_2: no`**, with the condition that fails cited by name if no.

### Step 3: Silence tune (conditional on Step 2)

Only apply if `SHIP_STEP_2: yes`:

- [ ] `grep -E "speech_silence_s|min_speech_silence_s" config/voice_coordinator_navigator.yaml` prints `0.8` and `0.5`.
- [ ] A test under `tests/` asserts the coordinator YAML has `speech_silence_s: 0.8` and `min_speech_silence_s: 0.5` (anchor test so a future edit cannot silently regress).
- [ ] After **another** ≥10 real calls post-tune, diary entry `docs/diary/YYYY-MM-DD-nc-235-post-tune.md` records new p50/p95 `nv_turn_wall_seconds` and any observed false-cutoff events (user speech truncated mid-utterance, counted from transcripts).

If `SHIP_STEP_2: no`:

- [ ] No config change is made.
- [ ] FR is closed as "measured; silence gate not dominant" with the actual breakdown cited in the diary.

### Tests

- [ ] `pytest tests/ -q --no-cov` green.
- [ ] `./run_tests.sh` green (excludes slow e2e + integration per session convention).

### Rollback

- [ ] Reverting the Step 3 commit restores 1.2/0.8 thresholds; no state migration.
- [ ] Reverting the Step 1 commit removes observation calls; histograms remain registered (no-op without `observe`).

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Observation adds per-turn overhead | `histogram.observe()` is ~µs; well below the 180ms FSM dispatch already measured. |
| Context key for `t_speech_complete` leaks across calls | Clear on observation; clear on call teardown; test asserts clearing. |
| 0.8s silence cuts mid-utterance "thinking" pauses → premature `speech_complete` | Adaptive 0.5s floor only applies to short utterances (≤20 chars); medium-length utterances keep 0.8s. Step 3 gate requires explicit false-cutoff review from transcripts on 10 post-tune calls. |
| Adaptive floor makes measurement noisy (two silence regimes interleaved) | Step 2 records the adaptive-path count separately; if >50% of turns hit the adaptive floor, split the histogram analysis by path in the diary entry. |
| ElevenLabs path instrumentation couples to voice_runtime internals | If `voice_runtime.tts.elevenlabs` has no clean first-chunk hook, instrument at the session boundary instead (`put_outbound_sync` wrapper), preserving the boundary rule. |
| User perception of "3s" doesn't actually correlate to `nv_turn_wall_seconds` | If Step 2 measurement shows p95 ≤ 1.7s but user still reports "3s", this FR closes with data showing latency is not the issue — something else (voice quality, phrasing of ack) drives the perception. Record and stop. |

## Notes

This FR exists because my initial latency estimate included "+0.4–0.7s TTS TTFB" for the ack path. The user correctly pointed out the ack is precanned mulaw; there is no TTFB. The corrected model puts the silence gate at 75% of perceptual latency, which makes it the only sensible next tune — but only *after* we can actually see the number change, because single-call analysis is not a tail statistic.

The Scripture: *May I normalize at the boundary, trusting no provider's type.* Here, the "provider" is my own mental model of the pipeline. Measurement is the boundary check.
