# VR-015 — Google STT speech-activity pulse

**Priority:** HIGH (blocks the CSAP Chirp trial NC-586)
**Type:** Bug fix in one provider. `GooglePersistentStt` gains a timer-driven `on_recognizing("")` pulse while Google reports speech in progress. No public API, constructor, configuration, transport or session change.
**Status:** Approved with revisions (2026-11-17); R-1…R-4 folded, owner answers recorded. Not implemented.
**Requested:** 2026-11-17
**Judgement:** [VR-015-google-stt-speech-activity-pulse.judgement.md](VR-015-google-stt-speech-activity-pulse.judgement.md)
**Owner and approver:** sami.j.p.heikkinen
**Parent:** [VR-013](VR-013-google-chirp-speech-providers.md) (Google providers, 0.5.0). Consumer evidence: CSAP NC-586 (`customer-service-agent-platform/feature-requests/NC-586-chirp3-test-stt.md`, evidence `feature-requests/evidence/NC-586-chirp3-test.md`).
**Effort:** half a day.
**Target release:** next **patch** after the release baseline at enforcement (expected 0.6.1), per `reference/release-checklist.md`.
**First consumer:** CSAP TEST (`STT_PROVIDER=google`). The owner keeps TEST on Chirp, unused by others, until this ships.

## Problem

Persistent-mode STT providers share one callback contract: `on_committed(text)` for finished text, `on_recognizing(text)` while the caller is still talking, `on_error(reason)` on a fatal fault. Consumers use `on_recognizing` as the "caller is still speaking" signal.

CSAP depends on that signal for turn taking. Its FSM ends a caller turn after 0.8–1.2 s with **no** STT event (CSAP `actions/real/silence_detector_action.py`, `config/voice_coordinator_navigator.yaml` `speech_silence_s: 1.2`, `min_speech_silence_s: 0.8`); every `on_recognizing` or `on_committed` resets that timer. CSAP uses only the timing: its consumer forwards `recognizing` with an empty payload (CSAP `services/stt.py`).

- **Azure** (`azure_stt.py:367-379`) fires `on_recognizing` with interim text about every 0.2–0.5 s while speech continues. In the 2026-11-16 desk check (CSAP `feature-requests/evidence/2026-11-16-chirp3-speech-check.md`) Azure produced `recognizing=46` for a ~20 s paragraph.
- **Google Chirp 3** returns no interim text, although `interim_results=True` is requested (`_google_config.py:134`). In the same desk check, the Azure-voiced paragraph produced `recognizing=7` on Google (one per detected speech start; evidence lines 95-105). In the provider, `SPEECH_ACTIVITY_BEGIN` produces one `on_recognizing("")` (`google_stt.py:409-411`); `SPEECH_ACTIVITY_END` is stored for `commit_lag_ms` only (`google_stt.py:412-413`) and never reaches the consumer.

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

While Google reports speech as active (between `SPEECH_ACTIVITY_BEGIN` and `SPEECH_ACTIVITY_END` or a final), `on_recognizing` fires at a steady rate, so a consumer's silence timer does not expire mid-utterance. Finished text is unchanged. This is not full Azure equivalence (VR-013 already noted differing callback cadence); the remaining gaps are listed under *Accepted limits*.

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
- At most one periodic producer exists at any time. A second BEGIN keeps its own immediate callback (existing behaviour), cancels the old periodic schedule and starts a new one from that BEGIN; the old schedule never emits again. The 0.25 s rate bound applies to periodic pulses only, not to immediate BEGIN callbacks or real interim results.
- Pulses fire exactly where the existing BEGIN callback fires: suppressed while `set_speaking(True)` is in effect, not suppressed by the echo discard window (same as Azure `_on_recognizing`, which checks only `_speaking`).
- The pulse is independent of interim results: if Google ever sends interim text, `_on_interim` forwards it as today and the pulse continues.

Why a pulse and not a new "speech ended" callback: the existing contract already carries the meaning ("still speaking"); a new callback would require every consumer to change its turn logic (CSAP's FSM spans about eight listening states). The fix is applied where Google's output enters voice_runtime, so consumers keep one contract for all providers.

### Accepted limits (owner decision 2026-11-17, R-1)

The pulse fills event gaps only while Google reports active speech. It does not pulse past `SPEECH_ACTIVITY_END`, and consumer timers are not changed to hide these gaps:

1. **END → final latency.** Google sends END 0.42–0.92 s before the final (B-1). The last pulse may come up to 0.25 s before END. CSAP does not wait for a final; it ends the turn once its threshold (0.8 s for short text) elapses (`silence_detector_action.py:67-97`). A brief caller pause can therefore still be cut off when the latency is long.
2. **Speech beyond the 30 s cap** loses the synthetic still-speaking signal.
3. **Stream replacement** (rotation, retry) ends the pulse; it resumes only at the next BEGIN on the new stream.

Whether the remaining cut-off rate is acceptable is measured in CSAP TEST under separate CSAP authority. Extending the signal through item 1 would be a separate FR.

### 2. Observability

- `INFO` on pulse start is not logged (one per utterance would be noise). The cap warning above is the only new log line.
- No metric; consumers already count `recognizing` events.

### 3. Documentation

- `CHANGELOG.md`: patch entry naming the behaviour change ("Google STT: `on_recognizing(\"\")` now repeats every 0.25 s while Google reports active speech, ending at speech end, a final, stream end, `stop()` or a 30 s cap; fills consumer silence-timer gaps during speech only"), plus the three accepted limits.
- `README.md` Google STT section: describe the pulse interval, its termination conditions, the cap and the accepted limits.

### Not changed

- Azure, ElevenLabs and mock providers; `stt_tee`; factories; `_google_config.py` (endpointing stays `supershort` by default; `interim_results` stays requested).
- Commit, echo-discard, rotation, reconnect and error semantics of `GooglePersistentStt`.
- Public constructor parameters and environment variables. Interval and cap are constants, not settings.

## Consumers and blast radius

| Consumer | Uses Google STT | Effect |
|---|---|---|
| CSAP | TEST only (`STT_PROVIDER=google`, NC-586); STG/PROD/Lapha use Azure | After a dependency bump, TEST receives events throughout active speech; the accepted limits still apply. Azure environments unaffected |
| csap-black-box-tests | No (ElevenLabs and mock STT) | None |
| outcaller | No | None |

## Acceptance criteria

Offline tests live in a new module `tests/test_vr015_google_stt_pulse.py`, marked `pytest.mark.req("VR-015")` (not the VR-013 marker). It reuses the harness of `tests/test_vr013_google_stt.py` by import, not by copy: `VirtualLoop`, `FakeSdk`, `Recorder`, `event()`, `final()`, `interim()`, `start`/`feed`/`stop` helpers and the `_clean_env`, `vt` and `sdk` fixtures (imported into the new module so pytest registers them), plus the same optional-Google import skip. No unrelated test is changed. RED commit first, GREEN second.

Synthetic timings below are relative to the BEGIN at `t=0`, in virtual seconds.

- [ ] AC-01 (runnable RED): with the Google extra installed, the new module collects and runs. On the unchanged 0.6.0 provider the tests fail on assertions about missing periodic callbacks, not on imports, fixtures or skips. RED recorded before GREEN.
- [ ] AC-02 (pulse): BEGIN emits one immediate empty notification, then one every 0.25 s. After draining all callbacks due through `t=2.0`, exactly nine notifications have occurred (1 + 8).
- [ ] AC-03 (termination by END or final): END stops periodic notifications, and the count stays stable for the next 2 s. A final without END also stops them; covered for a committed, an empty, a speaking-suppressed and an echo-discarded final. Existing commit and discard behaviour is unchanged.
- [ ] AC-04 (synthetic B-1 analogue, not an exact replay): commit "Kyllä." → BEGIN `t=0` → END `t=1.6` → final "juuri reseptin uusimisesta on kyse." `t=2.1`. Periodic pulses fire at `0.25, 0.50, …, 1.50`; none after END; the second final is committed exactly once at `t=2.1`. The END → final gap is intentionally not asserted.
- [ ] AC-05 (cap): with no END or final, the pulse terminates at `t=30.0`, emits nothing at or after that point and logs exactly one `WARNING` `Google STT speech pulse cap reached after 30s`. Advancing a further 2 s produces neither callbacks nor another warning.
- [ ] AC-06 (restart): BEGINs at `t=0` and `t=0.1` produce immediate callbacks at both, periodic callbacks at `0.35, 0.60, 0.85`, and none at `0.25, 0.50, 0.75`. Advancing past cancellation shows the old schedule cannot emit and at most one periodic producer is live.
- [ ] AC-07 (speaking, echo, interim): while `set_speaking(True)`, no pulse reaches the consumer and no extra producer is created. After `set_speaking(False)`, the next scheduled pulse fires, including inside the echo-discard window. Real interim text is forwarded unchanged; the periodic sequence continues, neither cancelled nor duplicated.
- [ ] AC-08 (lifecycle): explicit `stop()`, normal input EOF, terminal stream failure, retry and forced rotation each end the old pulse. No old callback occurs during teardown, backoff or the replacement stream before its own BEGIN; a BEGIN on the new stream starts exactly one new sequence. After `stop()`, no owned task is unfinished (VR-005 pattern).
- [ ] AC-09 (unchanged suite and checks): the full existing suite passes unchanged, including the VR-013 Google tests (run with the extra installed) and the Azure, ElevenLabs and mock tests. `ruff check voice_runtime/`, a clean wheel build and the PR checks of `reference/release-checklist.md` pass. Commands and outcomes are recorded in the evidence file.
- [ ] AC-10 (single live desk check, owner-authorized 2026-11-17): one invocation, no automatic retries, synthetic text only, no configuration or deployment change: `GOOGLE_SPEECH_PROJECT=scp-tenant-csap-test` (location `eu`, model `chirp_3`, `fi-FI`, endpointing `supershort`), `python -m voice_runtime.speech_check --provider azure --stt-provider google --text "<Run 3/4 paragraph of CSAP evidence 2026-11-16-chirp3-speech-check.md>" --timeout 45`. Pass: exit 0, `recognizing >= 20` (baseline 7) and WER `<= 0.156` (baseline: the Azure TTS → Google STT row, evidence line 105). The effective non-secret settings, package version, exact command, full output and transcript go in `feature-requests/evidence/VR-015-google-stt-speech-activity-pulse.md`. A failure is recorded as failed and blocks completion; it is not retried until it passes. Passing does not certify CSAP calls or clinical accuracy.
- [ ] AC-11 (docs and release): README and CHANGELOG describe the bounded timing change and its limits. The version is the next patch from the actual release baseline. The FR records implementation status, decisions and evidence. These are one-time evidence, not unit tests.

## Owner answers (gate C-2, 2026-11-17)

| Question | Answer |
|---|---|
| Accept that speech beyond the 30 s cap, END → final latency and stream replacement remain outside the pulse guarantee? | **Yes.** Measure the residual cut-off rate in CSAP TEST; an extension past END is a separate FR. |
| Authorize one Azure TTS / Google STT desk-check invocation and its spend? | **Yes**, one bounded invocation per AC-10. |

## Out of scope

- Any CSAP change, including the dependency bump and the NC-586 Phase B rerun (CSAP FR NC-586 or its successor).
- A new consumer callback for speech end, or exposing `SPEECH_ACTIVITY_END` to consumers.
- Changing endpointing defaults, phrase hints, model or location.
- Making interval or cap configurable.
- Investigating why Chirp 3 returns no interim text. If Google later returns it, AC-02 behaviour still holds.

## Risks

| # | Risk | Mitigation |
|---|---|---|
| K-1 | Noise produces BEGIN without END; pulses keep a consumer turn open | 30 s cap (AC-05); END and finals end the pulse earlier in normal speech (all 30 B-1 finals had END) |
| K-2 | Extra events load the consumer | 4 events/s, comparable to Azure's interim rate |
| K-3 | END → final latency (0.42–0.92 s in B-1) exceeds a consumer's short-turn threshold | Accepted limit 1; measured in CSAP TEST under separate authority; a follow-up FR may extend the signal |

## Source references

- `voice_runtime/providers/google_stt.py:405-445` (`_handle_response`, `_on_interim`, `_on_final`), `:128` (`_end_at`), `:172` (`stop`), `:187` (`set_speaking`), `:288-339` (`_run_stream`).
- `voice_runtime/providers/azure_stt.py:367-379` (`_on_recognizing`).
- `voice_runtime/providers/_google_config.py:134` (`interim_results=True`).
- `tests/test_vr013_google_stt.py:63-306` (virtual loop and response builders).
