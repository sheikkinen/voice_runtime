# VR-014 — Cross-provider speech check CLI

**Priority:** MEDIUM
**Type:** Feature. Adds a packaged diagnostic module that uses only the existing public provider API. No provider, transport, session or protocol changes.
**Status:** Proposed (not judged).
**Requested:** 2026-11-16
**Jira:** VBOT-212 (manual verification tool), under epic VBOT-210. It is the manual building block for VBOT-213 (failover planning).
**Parent:** [VR-012](VR-012-google-chirp-stt-tts-providers.md), split by its [round-1 judgement](VR-012-google-chirp-stt-tts-providers.judgement.md). This FR owns D-3 and folds R-2, R-5, R-6 (CLI parts) and R-7. It does not depend on [VR-013](VR-013-google-chirp-speech-providers.md): offline acceptance and the Azure/ElevenLabs live rows need nothing from it. The Google rows apply once VR-013 has shipped.
**Effort:** 1.5 days.
**Target release:** the next **minor** release after the actual release baseline at enforcement (`reference/release-checklist.md`).
**First consumer:** the owner and operators. The tool runs from a workstation or inside a consumer pod (CSAP worker, black-box tester) with that pod's credentials, to answer "is provider X working right now?" during release checks and incidents.
**Prior art:** `scripts/probe_vr009_lid.py` (20 ms frames, trailing silence, version header); `voice_runtime/audio.py` (`_linear_to_ulaw`); `VoiceSession.set_mixer`/`tap_agent` (capture hook); `send_mark_and_wait`/`put_outbound_sync`, which do nothing when the session has no event loop.
**Judgement:** none yet.

## Problem and ideal result

There is no single way to check that the speech providers voice_runtime ships actually work. Checking Azure STT takes `scripts/probe_vr009_lid.py`. Azure TTS and both ElevenLabs providers have no probe at all, and Google is checked with ad-hoc curl. During an incident ("is Azure down, would ElevenLabs work?") an operator would have to write code.

**Ideal result:** `python -m voice_runtime.speech_check` synthesises, recognises, or does both (round trip) for each selected installed provider. It prints one comparison row per provider and step, and its exit code means:

- **pass:** every provider that ran passed;
- **fail:** something that ran failed;
- **nothing runnable:** no selected provider could run.

It ships in the wheel, so it can run where the consumer runs.

## Proposed solution

### 1. Module and interface

`voice_runtime/speech_check.py`, run as `python -m voice_runtime.speech_check`. There is no console-script entry point.

```text
--provider NAME        repeatable; azure | elevenlabs | google | all (default all)
--mode MODE            tts | stt | roundtrip (default roundtrip)
--text TEXT            reference/synthesis text; default a fixed synthetic Finnish sentence without digits
--audio PATH           required in stt mode, rejected otherwise
--stt-provider NAME    roundtrip only: recogniser for every TTS output (default: same provider)
--out DIR              tts/roundtrip only: write <provider>.ulaw and <provider>.wav
--timeout SECONDS      per provider step, finite and > 0 (default 30)
--max-wer RATIO        roundtrip/stt-with-text only, finite and >= 0 (default 0.2)
--json                 every stdout line is one JSON object
```

Anything else is a usage error (exit 2):

- an incompatible option for the chosen mode;
- a non-finite or out-of-range number;
- a `--text` that is empty after normalisation, in roundtrip mode or in stt mode where it serves as the reference;
- an unknown provider.

**Audio input** (`--audio`). Three formats are accepted:

- `.ulaw`: raw 8 kHz mono μ-law;
- WAV with format tag 7 (μ-law), 8000 Hz, mono;
- WAV with format tag 1 (PCM), 16-bit little-endian, 8000 Hz, mono. It is converted with `voice_runtime.audio._linear_to_ulaw` per sample.

Any other file (other rates, stereo, other bit depths or encodings, malformed headers) → exit 2 with the reason.

### 2. Steps

Each provider step runs in a **child process** (`multiprocessing`, spawn start method). The step is bounded by one monotonic deadline of `--timeout` seconds. The deadline covers construction, `start`, synthesis, feeding, settling and `stop`. On expiry the parent terminates the child (then kills it if it does not exit within 2 s), records **FAIL timeout**, and continues. This is the only way to guarantee that a blocked synchronous SDK call stops making provider requests (R-5).

- **TTS.**
  - A `VoiceSession()` with no event loop, and a capture mixer passed to `set_mixer`. The mixer's `write_agent` appends the bytes; `write_caller` ignores them.
  - Call `create_tts(p).speak(text, session, threading.Event())`.
  - Record: time to first captured byte, total time, captured bytes and audio seconds, and the result dict keys.
  - **FAIL** if nothing was captured, the result has `error`, or the provider's `on_error` fired.
- **STT.**
  - `get_stt_class(p)()`, with `on_committed`, `on_recognizing` and `on_error` recorded.
  - `await start(queue)`, then feed 160-byte frames every 20 ms, then 3 s of `0xFF`, then `None`.
  - Settle until there has been no new commit for 2 s (within the deadline), then `await stop()` in a `finally` that runs on every exit after a successful `start`.
  - Record: the commits, the number of recognizing events, the time from end of speech to the last commit, and errors.
  - **FAIL** if there are no commits, `on_error` fired, any exception was raised, or `stop()` raised.
- **Roundtrip.**
  - The TTS output becomes the STT input of `--stt-provider` (default: same provider).
  - WER is the word-level Levenshtein distance divided by the number of reference words. Both sides are lowercased, punctuation is stripped and whitespace collapsed.
  - **FAIL** if WER > `--max-wer`.
  - The row names both the synthesiser and the recogniser.

### 3. SKIP versus FAIL (R-5)

**SKIP** is allowed only for absent local configuration, checked *before* any request:

- the provider's import fails (extra not installed);
- `azure`: `AZURE_SPEECH_KEY` unset;
- `elevenlabs`: `ELEVENLABS_API_KEY` unset;
- `google`: neither `GOOGLE_SPEECH_PROJECT` nor `GOOGLE_CLOUD_PROJECT` set, or `google.auth.default()` raises `DefaultCredentialsError`.

In roundtrip, both the synthesiser and the recogniser are pre-checked; the SKIP reason names whichever is missing. SKIP reasons name variables, never values.

Everything after a request has been attempted is **FAIL**, including authentication rejection and permission, region or quota errors.

### 4. Output

- **Table mode.**
  - stderr gets the header:
    - voice_runtime version and its `__file__`;
    - the git SHA, only if the package directory is inside a git checkout whose top-level `pyproject.toml` declares `name = "voice-runtime"` (otherwise `sha=none`; never an unrelated surrounding checkout);
    - the installed SDK versions;
    - the Google project and location if selected;
    - `STT_LANGUAGE_CODE` (the ElevenLabs STT language), printed so a language mismatch is visible.
  - stdout gets the table: `provider | stt_provider | step | status | first_audio_ms | total_ms | commits | lag_ms | wer | detail`.
  - Transcripts go in `detail`. The input is synthetic text or a file the operator chose; the README warns against pointing `--audio` at caller recordings on shared terminals.
- **`--json` mode.**
  - stdout has a `{"type":"meta",…}` object first, then one `{"type":"result",…}` per row.
  - All logs and headers go to stderr.
- **Exit codes.**
  - `0`: at least one step ran and every step that ran passed. SKIPs are allowed. This is the general-purpose incident policy.
  - `1`: any FAIL.
  - `2`: a usage error, or every selected step was SKIP.

  A SKIP means "not validated". AC-07 (live acceptance) requires PASS for every intended combination, not exit 0.

### Not changed

- Providers, transports, `VoiceSession` and the protocols.
- Factories and dependencies: no new runtime dependency.
- CI.

## Acceptance criteria

- [ ] **AC-01 (behavioural RED, R-2).** A test-only commit adds `tests/test_vr014_speech_check.py`.
  - Tests call `voice_runtime.speech_check` through `importlib.import_module` inside one module-scoped helper. In RED the only allowed failure is that module's `ModuleNotFoundError`, reported by the helper as `pytest.fail("speech_check missing")`, plus assertion failures. A collection error, a fixture error, or an import error of any *other* module makes the RED invalid.
  - Why a missing module counts as RED here: a new module has no earlier seam. The absent module is the missing implementation itself (doctrine criterion 8 forbids failures from import errors that are *not* missing implementation). Everything else must fail on assertions.
  - Every test carries `@pytest.mark.req("VR-014")`.
  - No network: providers are `voice_runtime.mock` providers or fakes put in place by patching the existing factories.
- [ ] **AC-02 (parsing and validation).** A table-driven test covers every §1 usage error, with exit 2 and a reason. Examples:
  - `--mode stt` without `--audio`; `--audio` with `--mode tts`;
  - `--stt-provider` outside roundtrip;
  - `--timeout 0`, `-1`, `nan`, `inf`; `--max-wer -0.1`, `nan`;
  - an unknown provider;
  - `--text "  ..  "` in roundtrip.
- [ ] **AC-03 (audio input).**
  - Fixtures are built in the test: raw `.ulaw`, μ-law WAV and PCM16 WAV at 8 kHz mono are accepted.
  - The PCM samples `0`, `1000`, `-1000`, `32767` and `-32768` convert to the same bytes `_linear_to_ulaw` gives.
  - 16 kHz, stereo, 8-bit PCM, IEEE-float and truncated-header WAVs → exit 2.
- [ ] **AC-04 (WER).** Covers:
  - identical → 0.0;
  - one substitution in five words → 0.2;
  - one deletion → 0.2;
  - case and punctuation ignored;
  - an empty hypothesis → 1.0.
- [ ] **AC-05 (steps).**
  - The TTS step captures through the real loop-less `VoiceSession` and `set_mixer` with `MockTts` or a fake that calls `tap_agent`.
  - The STT step against a fake provider delivers commits.
  - `stop()` is called after a successful `start` when the feed raises, when a callback raises, when the deadline hits, and on normal completion.
  - A `stop()` that raises → FAIL.
  - A roundtrip with `--stt-provider` other than the synthesiser names both in the row and pre-checks both.
- [ ] **AC-06 (deadline and process bound).**
  - A fake synchronous TTS that blocks forever, and a fake async STT whose `start` never returns, each produce FAIL timeout.
  - The command exits within `--timeout + 3` s.
  - The child process is no longer alive afterwards (checked by PID).
- [ ] **AC-07 (SKIP, FAIL and exits).**
  - Missing env for each provider → SKIP naming the variable, and the output does not contain a planted sentinel key value.
  - A fake that raises a permission error after construction → FAIL.
  - Exit `0` (one pass plus one skip), `1` (one fail), `2` (all skip).
  - In `--json` mode every non-empty stdout line parses with `json.loads` as an object, with `meta` first.
  - The SHA is `none` when the package is loaded from a temporary copy outside any voice_runtime checkout.
- [ ] **AC-08 (artifacts).**
  - The full suite passes in fresh 3.11 and 3.12 venvs with `.[dev,azure,elevenlabs]`.
  - `ruff check voice_runtime/` passes.
  - `python -m build` and `twine check` pass.
  - A clean non-editable wheel install runs `python -m voice_runtime.speech_check --help`, and `--provider azure --mode tts` with `AZURE_SPEECH_KEY` unset (→ exit 2, SKIP), from outside the repo.
- [ ] **AC-09 (live, owner-approved, R-6).** Only after Q1 is answered yes, run with `STT_LANGUAGE_CODE` set explicitly to the ElevenLabs Finnish code and recorded:

  ```bash
  python -m voice_runtime.speech_check --provider azure --provider elevenlabs --mode roundtrip --out tmp/vr014
  python -m voice_runtime.speech_check --provider azure --mode roundtrip --stt-provider elevenlabs
  python -m voice_runtime.speech_check --provider elevenlabs --mode roundtrip --stt-provider azure
  ```

  - Every row must be **PASS**. A SKIP or FAIL blocks release and is recorded as failure evidence.
  - Record the full output, versions, SHA and the Azure region.
  - Once VR-013 has shipped, the same matrix with `google` added in every position is recorded as a follow-up row set. It is not a gate for this FR's release.
- [ ] **AC-10 (docs).**
  - `README.md` gains a speech-check section covering:
    - the usage and an example table;
    - SKIP/FAIL semantics and exit codes, including "SKIP is not validation";
    - the per-step child-process timeout;
    - the warning about caller recordings.
  - `CHANGELOG.md` records VR-014 under the actual next minor; the version is bumped. Tagging and publishing need the owner's explicit "publish".
- [ ] **AC-11 (record).** This FR records RED/GREEN commits, decisions, suite results, AC-08 and AC-09 output, the owner answer to Q1, and deviations.

## Owner question (R-7). Answer and record before AC-09

1. **Q1.** May the Azure subscription (CSAP `.env` key, region `swedencentral`) and the ElevenLabs account be used for the AC-09 billable synthetic-only runs? (Google is covered by VR-013 Q1 and by this FR's follow-up rows.)

## Out of scope

- Google providers (VR-013).
- Automatic failover and scheduling (VBOT-213).
- Number normalisation in WER.
- Audio formats beyond §1.
- A console-script entry point.
- CI changes.
- Tags and publication.

## Source references

- `voice_runtime/session.py` (`VoiceSession` dataclass defaults; `set_mixer`, `tap_agent`; `put_outbound_sync` and `send_mark_and_wait` do nothing when there is no loop)
- `voice_runtime/stt.py`, `voice_runtime/tts.py` (factories); `voice_runtime/mock/stt.py`, `voice_runtime/mock/tts.py`
- `voice_runtime/providers/elevenlabs_stt.py` (`STT_LANGUAGE_CODE`, default `en`); `azure_tts.py`/`azure_stt.py` (`AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION`)
- `voice_runtime/audio.py` (`_linear_to_ulaw`)
- `scripts/probe_vr009_lid.py`; `reference/release-checklist.md`
