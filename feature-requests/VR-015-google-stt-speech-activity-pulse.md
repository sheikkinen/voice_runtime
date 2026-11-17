# VR-015 — Google STT speech-activity pulse

**Priority:** HIGH (blocks the CSAP Chirp trial NC-586)
**Type:** Bug fix in one provider. `GooglePersistentStt` gains a timer-driven `on_recognizing("")` pulse while Google reports speech in progress. No public API, constructor, configuration, transport or session change.
**Status:** Proposed (2026-11-17)
**Requested:** 2026-11-17
**Owner and approver:** sami.j.p.heikkinen
**Parent:** [VR-013](VR-013-google-chirp-speech-providers.md) (Google providers, 0.5.0). Consumer evidence: CSAP NC-586 (`customer-service-agent-platform/feature-requests/NC-586-chirp3-test-stt.md`, evidence `feature-requests/evidence/NC-586-chirp3-test.md`).
**Effort:** half a day.
**Target release:** next **patch** after the release baseline at enforcement (expected 0.6.1), per `reference/release-checklist.md`.
**First consumer:** CSAP TEST (`STT_PROVIDER=google`). The owner keeps TEST on Chirp, unused by others, until this ships.

## Problem

Persistent-mode STT providers share one callback contract: `on_committed(text)` for finished text, `on_recognizing(text)` while the caller is still talking, `on_error(reason)` on a fatal fault. Consumers use `on_recognizing` as the "caller is still speaking" signal.

CSAP depends on that signal for turn taking. Its FSM ends a caller turn after 0.8–1.2 s with **no** STT event (CSAP `actions/real/silence_detector_action.py`, `config/voice_coordinator_navigator.yaml` `speech_silence_s: 1.2`, `min_speech_silence_s: 0.8`); every `on_recognizing` or `on_committed` resets that timer. CSAP uses only the timing: its consumer forwards `recognizing` with an empty payload (CSAP `services/stt.py`).

- **Azure** (`azure_stt.py:367-379`) fires `on_recognizing` with interim text about every 0.2–0.5 s while speech continues. In the 2026-11-16 desk check (CSAP `feature-requests/evidence/2026-11-16-chirp3-speech-check.md`) Azure produced `recognizing=46` for a ~20 s paragraph.
- **Google Chirp 3** returns no interim text, although `interim_results=True` is requested (`_google_config.py:134`). The same desk check shows `recognizing=1`. In the provider, `SPEECH_ACTIVITY_BEGIN` produces one `on_recognizing("")` (`google_stt.py:409-411`); `SPEECH_ACTIVITY_END` is stored for `commit_lag_ms` only (`google_stt.py:412-413`) and never reaches the consumer.

With the default `supershort` endpointing, Chirp finalises at short pauses inside a sentence. After such a final, the continuation produces one `on_recognizing("")` at its BEGIN and then nothing until its own final. If the continuation lasts longer than the consumer's silence threshold, the consumer ends the turn while the caller is still talking.

Live evidence, CSAP TEST 2026-11-17, call `CA6cb01453dfaf58154b9a46a82a211a17`, pod log of worker 0, image voice-runtime 0.6.0:

| Time (UTC) | Provider event |
|---|---|
| 07:47:16.888 | final "Kyllä." |
| 07:47:16.934 | `on_recognizing("")` (BEGIN of the continuation) |
| — | nothing for 1.6 s; CSAP ends the turn and the bot speaks at ~07:47:17 |
| 07:47:18.551 | final "juuri reseptin uusimisesta on kyse." |

Over the call: 19 `on_recognizing` events, all empty; 19 finals. Over the pod log window: 30 finals, all with `commit_lag_ms` ≥ 0 (420–924 ms), i.e. `SPEECH_ACTIVITY_END` preceded every final. The owner's two manual calls showed the same fragmentation ("google doesnt let the caller speak till end").

## Ideal result

From a consumer's point of view, Google behaves like Azure: while the caller is speaking, `on_recognizing` fires at a steady rate, so a silence timer measures real silence. Finished text is unchanged.

## Proposed solution

### 1. Pulse while speech is active

In `voice_runtime/providers/google_stt.py`:

- New module constant `SPEECH_PULSE_INTERVAL_S = 0.25` and `SPEECH_PULSE_MAX_S = 30.0`.
- On `SPEECH_ACTIVITY_BEGIN` (existing branch, `_handle_response`): keep the existing immediate `on_recognizing("")`, and start (or restart) one pulse task, scheduled through `_schedule_owned` so `stop()` cancels it (VR-005 ownership).
- The pulse task loops: `await _sleep(SPEECH_PULSE_INTERVAL_S)`; if `self._speaking` is false and `on_recognizing` is set, call `on_recognizing("")`. The task uses the module `_sleep` and `_monotonic` seams so tests run on the virtual loop.
- The pulse ends at the first of:
  1. `SPEECH_ACTIVITY_END`;
  2. a final result (`_on_final`), whatever its text, including discarded or empty finals;
  3. the end of the current stream (rotation, retry, end of input; the `finally` block of `_run_stream`);
  4. `stop()`;
  5. `SPEECH_PULSE_MAX_S` after its start (guard against a BEGIN never followed by END or final, e.g. line noise). Reaching the cap logs one `WARNING` `Google STT speech pulse cap reached after %.0fs`.
- At most one pulse task exists at any time. A second BEGIN restarts it; it never runs two.
- Pulses fire exactly where the existing BEGIN callback fires: suppressed while `set_speaking(True)` is in effect, not suppressed by the echo discard window (same as Azure `_on_recognizing`, which checks only `_speaking`).
- The pulse is independent of interim results: if Google ever sends interim text, `_on_interim` forwards it as today and the pulse continues.

Why a pulse and not a new "speech ended" callback: the existing contract already carries the meaning ("still speaking"); a new callback would require every consumer to change its turn logic (CSAP's FSM spans about eight listening states). The fix is applied where Google's output enters voice_runtime, so consumers keep one contract for all providers.

### 2. Observability

- `INFO` on pulse start is not logged (one per utterance would be noise). The cap warning above is the only new log line.
- No metric; consumers already count `recognizing` events.

### 3. Documentation

- `CHANGELOG.md`: patch entry naming the behaviour change ("Google STT: `on_recognizing` now pulses every 0.25 s between speech activity begin and end; consumers using it as a still-speaking signal no longer cut callers off").
- `README.md` Google STT section: one sentence describing the pulse and its interval and cap.

### Not changed

- Azure, ElevenLabs and mock providers; `stt_tee`; factories; `_google_config.py` (endpointing stays `supershort` by default; `interim_results` stays requested).
- Commit, echo-discard, rotation, reconnect and error semantics of `GooglePersistentStt`.
- Public constructor parameters and environment variables. Interval and cap are constants, not settings.

## Consumers and blast radius

| Consumer | Uses Google STT | Effect |
|---|---|---|
| CSAP | TEST only (`STT_PROVIDER=google`, NC-586); STG/PROD/Lapha use Azure | TEST turn taking fixed after a dependency bump; Azure environments unaffected |
| csap-black-box-tests | No (ElevenLabs and mock STT) | None |
| outcaller | No | None |

## Acceptance criteria

All offline tests use the existing virtual-loop harness of `tests/test_vr013_google_stt.py` (`VirtualLoop`, `FakeSdk`, `event()`, `final()`, `interim()`), in a new file `tests/test_vr015_google_stt_pulse.py`. RED commit first (tests failing on 0.6.0 behaviour), GREEN commit second.

- [ ] AC-01 (pulse): after BEGIN with no END or final, `on_recognizing("")` fires once at BEGIN and then every 0.25 s of virtual time; over 2.0 s the count is 1 + 8.
- [ ] AC-02 (end by END): after END, no further pulse fires (count stable over the next 2.0 s).
- [ ] AC-03 (end by final): a final without a preceding END stops the pulse; covered for a committed final, an empty final and a final discarded by the echo window.
- [ ] AC-04 (B-1 reproduction): the sequence final "Kyllä." → BEGIN → 1.6 s → END → final "juuri…" yields no gap longer than 0.25 s between consecutive consumer events (`on_recognizing` or `on_committed`) from BEGIN to the second final.
- [ ] AC-05 (cap): BEGIN with no END or final stops pulsing at 30.0 s and logs exactly one cap `WARNING`.
- [ ] AC-06 (single task): BEGIN, BEGIN (0.1 s apart) produce one pulse sequence, not two (no more than one pulse per 0.25 s).
- [ ] AC-07 (speaking): while `set_speaking(True)`, no pulse reaches the consumer; after `set_speaking(False)`, pulses resume if speech is still active.
- [ ] AC-08 (lifecycle): `stop()` during an active pulse leaves no pending task (the existing VR-005 owned-futures assertion pattern); a stream rotation or retry during an active pulse ends it.
- [ ] AC-09 (unchanged): the full existing suite passes unchanged, including `tests/test_vr013_google_stt.py`; no Azure, ElevenLabs or mock test is edited.
- [ ] AC-10 (live desk check): `python -m voice_runtime.speech_check --provider azure --stt-provider google --text "<NC-586 evidence paragraph>" --timeout 45` against project `scp-tenant-csap-test` reports `recognizing` ≥ 20 for the ~20 s paragraph (0.6.0: 1), with the transcript and WER no worse than the 2026-11-16 Google row. Output recorded in `feature-requests/evidence/`.
- [ ] AC-11 (docs and release): CHANGELOG and README updated; version bumped as a patch per the release checklist.

## Out of scope

- Any CSAP change, including the dependency bump and the NC-586 Phase B rerun (CSAP FR NC-586 or its successor).
- A new consumer callback for speech end, or exposing `SPEECH_ACTIVITY_END` to consumers.
- Changing endpointing defaults, phrase hints, model or location.
- Making interval or cap configurable.
- Investigating why Chirp 3 returns no interim text. If Google later returns it, AC-01 behaviour still holds.

## Risks

| # | Risk | Mitigation |
|---|---|---|
| K-1 | Noise produces BEGIN without END; pulses keep a consumer turn open | 30 s cap (AC-05); END and finals end the pulse earlier in normal speech (all 30 B-1 finals had END) |
| K-2 | Extra events load the consumer | 4 events/s, comparable to Azure's interim rate |
| K-3 | END arrives late, delaying turn end | Turn end then waits for the final anyway (≈0.5 s after END in B-1); measured in NC-586 Phase B |

## Source references

- `voice_runtime/providers/google_stt.py:405-445` (`_handle_response`, `_on_interim`, `_on_final`), `:128` (`_end_at`), `:172` (`stop`), `:187` (`set_speaking`), `:288-339` (`_run_stream`).
- `voice_runtime/providers/azure_stt.py:367-379` (`_on_recognizing`).
- `voice_runtime/providers/_google_config.py:134` (`interim_results=True`).
- `tests/test_vr013_google_stt.py:63-306` (virtual loop and response builders).
