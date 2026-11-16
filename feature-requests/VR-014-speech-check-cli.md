# VR-014 — Cross-provider speech check CLI

**Priority:** MEDIUM
**Type:** Feature. Adds a packaged diagnostic module that uses only the existing public provider API. No provider, transport, session or protocol changes.
**Status:** Implemented (2026-11-16), local branch `feat/vr-014-speech-check-cli` stacked on VR-013, version 0.6.0, unpushed. AC-01 to AC-11 met; AC-10 has one recorded FAIL accepted by the owner (see Implementation record). Tagging and publishing await the owner's explicit "publish".
**Requested:** 2026-11-16
**Jira:** VBOT-212 (manual verification tool), under epic VBOT-210. It is the manual building block for VBOT-213 (failover planning).
**Parent:** [VR-012](VR-012-google-chirp-stt-tts-providers.md), split by its [round-1 judgement](VR-012-google-chirp-stt-tts-providers.judgement.md). This FR owns VR-012's D-3 and its revisions R-2, R-5, R-6 (CLI parts) and R-7. In the rest of this FR, R-1 to R-6 mean the revisions of this FR's own judgement. It does not depend on [VR-013](VR-013-google-chirp-speech-providers.md): offline acceptance and the Azure/ElevenLabs live rows need nothing from it. Until VR-013 has shipped, `google` is reported as SKIP (provider not in this version); its rows apply afterwards.
**Effort:** 2–2.5 days: CLI, spawn supervisor, the offline harness and the Python 3.11/3.12 matrix.
**Target release:** the next **minor** release after the actual release baseline at enforcement (`reference/release-checklist.md`).
**First consumer:** the owner and operators. The tool runs from a workstation or inside a consumer pod (CSAP worker, black-box tester) with that pod's credentials, to answer "is provider X working right now?" during release checks and incidents.
**Prior art:** `scripts/probe_vr009_lid.py` (20 ms frames, trailing silence, version header); `voice_runtime/audio.py` (`_linear_to_ulaw`); `VoiceSession.set_mixer`/`tap_agent` (capture hook); `send_mark_and_wait`/`put_outbound_sync`, which do nothing when the session has no event loop.
**Judgement:** [round 1](VR-014-speech-check-cli.judgement.md): APPROVED WITH REVISIONS; promoted by the owner on 2026-11-16. Revisions R-1 to R-6 below refer to that judgement.

## Problem and ideal result

There is no single way to check that the speech providers voice_runtime ships actually work. Checking Azure STT takes `scripts/probe_vr009_lid.py`. Azure TTS and both ElevenLabs providers have no probe at all, and Google is checked with ad-hoc curl. During an incident ("is Azure down, would ElevenLabs work?") an operator would have to write code.

**Ideal result:** `python -m voice_runtime.speech_check` synthesises, recognises, or does both (round trip) for each selected installed provider. It prints one comparison row per provider and step, and its exit code means:

- **pass:** every provider that ran passed;
- **fail:** something that ran failed;
- **nothing runnable:** no selected provider could run.

It ships in the wheel, so it can run where the consumer runs.

## Proposed solution

### 1. Module and interface

`voice_runtime/speech_check.py`, run as `python -m voice_runtime.speech_check`. There is no console-script entry point. All helpers are private.

```text
--provider NAME        repeatable; azure | elevenlabs | google | all (default all)
--mode MODE            tts | stt | roundtrip (default roundtrip)
--text TEXT            synthesis text and WER reference (see Reference text)
--audio PATH           required in stt mode, rejected otherwise
--stt-provider NAME    roundtrip only: recogniser for every TTS output (default: same provider)
--out DIR              tts/roundtrip only: write <provider>.ulaw and <provider>.wav
--timeout SECONDS      per provider step, finite and > 0 (default 30)
--max-wer RATIO        finite and >= 0 (default 0.2); only where a reference applies
--json                 every stdout line is one JSON object
```

**Provider selection (R-4).** `all` expands to `azure`, `elevenlabs`, `google`, in that order. The selection is deduplicated, keeping the first occurrence: `--provider google --provider all` runs `google`, `azure`, `elevenlabs`.

**Reference text (R-4).**

- In `tts` and `roundtrip` mode, an omitted `--text` uses the fixed default sentence `Hyvää huomenta, haluaisin varata ajan lääkärille ensi viikolla.` It is Finnish, uses `ä`, and has no digits.
- In `stt` mode, an omitted `--text` means **no reference**: no WER is computed and the WER field is not applicable. A given `--text` is the reference, scored like roundtrip.
- An explicit `--max-wer` with no applicable reference (`tts` mode, or `stt` mode without `--text`) is a usage error.

**Usage errors (exit 2, with the reason):**

- an incompatible option for the chosen mode, including the `--max-wer` rule above;
- a non-finite or out-of-range number;
- a `--text` that is empty after normalisation (§2 Scoring), wherever it is used;
- an unknown provider name (not one of the four above).

**Audio input** (`--audio`). Three formats are accepted:

- `.ulaw`: raw 8 kHz mono μ-law;
- WAV with format tag 7 (μ-law), 8000 Hz, mono;
- WAV with format tag 1 (PCM), 16-bit little-endian, 8000 Hz, mono. It is converted with `voice_runtime.audio._linear_to_ulaw` per sample.

Any other file (other rates, stereo, other bit depths or encodings, malformed headers) → exit 2 with the reason.

**Audio output** (`--out`, R-6). For each TTS step, `<provider>.ulaw` holds the captured bytes unchanged, and `<provider>.wav` holds the same bytes as a WAV with format tag 7, 8000 Hz, mono, 8 bits (the layout of `voice_runtime.audio._write_wav_header`). A write error makes that TTS row FAIL with the reason; it is never a traceback followed by success.

### 2. Steps and supervisor

**Supervisor (R-2, R-3).** Each provider step runs in a **child process** started with the `spawn` method.

- **Child entry.** The child target is the real private step worker. The supervisor takes a private keyword argument `_child_target` (default: the real worker) so tests can pass an importable, test-only entry point. That entry point installs fake providers **inside the child** and then calls the real step worker. There is no CLI option, environment variable or plugin mechanism for this.
- **Everything runs in the child, inside the deadline.** Imports, factory resolution, preflight (including ADC discovery), construction, `start`, synthesis, feeding, settling and `stop`. The parent does no credential lookup.
- **Result transfer.** The child sends its result (including captured audio) over a `multiprocessing` pipe. The parent reads the pipe while waiting, before joining the child, so a result larger than the pipe buffer cannot block the child and look like a timeout.
- **Deadline sequence.** Each step has its own monotonic deadline of `--timeout` seconds from child start. At the deadline:
  1. the row is recorded as **FAIL timeout**, and the parent sets a cancel event shared with the child;
  2. the child gets at most **1 s** of cooperative teardown: async work is cancelled, which runs `finally` and awaits `stop()`; a synchronous `speak` gets its `stop_event` set;
  3. if still alive, `terminate()`, then at most **1 s** more;
  4. if still alive, `kill()`, then the child is reaped.

  The step is over, and the child reaped, within `--timeout + 3` s. After a timeout, the next selected provider still runs.
- **Limits stated plainly.** A terminated or killed child does not run Python `finally`, so `stop()` is only guaranteed on normal exit and cooperative cancellation. Process death stops further local work; it does not retract requests a remote service already accepted. A `stop()` that blocks past the budget or raises is FAIL.

**Steps.**

- **TTS.**
  - A `VoiceSession()` with no event loop, and a capture mixer passed to `set_mixer`. The mixer's `write_agent` appends the bytes; `write_caller` ignores them.
  - Call `create_tts(p).speak(text, session, stop_event)`, where `stop_event` is set by cooperative cancellation.
  - Record: time to first captured byte, total time, captured bytes, audio seconds (bytes / 8000), and the result dict keys.
  - **FAIL**, each cause on its own: nothing captured; the result has `error`; the provider's `on_error` fired (even when bytes were captured); an output write failed.
- **STT.**
  - `get_stt_class(p)()`, with `on_committed`, `on_recognizing` and `on_error` recorded.
  - `await start(queue)`, then feed 160-byte frames every 20 ms, then 3 s of `0xFF`, then `None`.
  - **End of input** is the instant the last original audio frame is fed, before the appended silence.
  - **Settling.** The quiet window starts when feeding (including the silence) completes and restarts on every later commit. The step ends after 2 s with no new commit, or at the deadline.
  - `await stop()` in a `finally` that runs on every normal or cooperative exit after a successful `start`.
  - Record: the commits in arrival order, the number of recognizing events, and `lag_ms` from end of input to the last commit.
  - **FAIL**, each cause on its own: no commits; `on_error` fired (even when another commit arrived); any exception; `stop()` raised or blocked.
- **Roundtrip.** For each synthesiser and recogniser pair, a TTS step, then an STT step on its captured audio, as two rows. The STT row names both providers and carries the WER.

**Scoring (R-4).**

- **Normalisation:** lowercase; delete every character whose Unicode category starts with `P`; split on whitespace and rejoin with single spaces. Letters such as `ä` and `ö` are kept. Numbers are not normalised.
- **Hypothesis:** the commits, in arrival order, joined with one space, then normalised.
- **WER:** word-level Levenshtein distance (substitutions, deletions and insertions) divided by the number of reference words.
- **FAIL** only when WER is strictly greater than `--max-wer`; WER equal to the threshold passes.

**Rows (R-4).**

- `tts` and `stt` mode: one row per selected provider.
- `roundtrip`: per pair, a TTS row, then an STT row.
- If the TTS step fails or is skipped, the STT step does not run; its row is **SKIP** with the reason `dependency: tts <status>`. This dependency SKIP is reported separately from a configuration SKIP, and a failed TTS row still makes the exit code 1.

### 3. SKIP versus FAIL (R-5)

Preflight runs in the step's child, inside its deadline, before any speech request, and checks only what the mode needs. In roundtrip, the TTS step's preflight checks **both** the synthesiser and the recogniser before any synthesis request. If the recogniser cannot run, both rows are SKIP and no synthesis request is made.

**SKIP** is allowed only for these positively identified causes:

| Cause | How it is identified |
|---|---|
| Provider not in this voice_runtime version | `importlib.util.find_spec` finds no provider module for that name (for example `voice_runtime.providers.google_stt` before VR-013). Factories are not changed and no Google stub ships. |
| Optional extra not installed | `importlib.util.find_spec` of the provider's declared dependency module finds nothing: `azure.cognitiveservices.speech` for `azure`, `elevenlabs` for `elevenlabs`, `google.cloud.speech_v2` (STT) or `google.cloud.texttospeech` (TTS) for `google`. |
| Missing configuration | `azure`: `AZURE_SPEECH_KEY` empty or unset. `elevenlabs`: `ELEVENLABS_API_KEY` empty or unset. `google`: neither `GOOGLE_SPEECH_PROJECT` nor `GOOGLE_CLOUD_PROJECT` non-empty. |
| No ADC (`google` only) | `google.auth.default()` raises `google.auth.exceptions.DefaultCredentialsError`. |

**Everything else is FAIL**, including:

- any other `ImportError` or exception while importing or constructing a provider, even when the dependency is present;
- any other exception from ADC discovery;
- authentication, permission, region or quota errors once a request has been attempted.

SKIP reasons name variables and modules, never values.

### 4. Output

- **Table mode.**
  - stderr gets the header:
    - voice_runtime version and its `__file__`;
    - the git SHA, only if the package directory is inside a git checkout whose top-level `pyproject.toml` declares `name = "voice-runtime"` (otherwise `sha=none`; never an unrelated surrounding checkout);
    - the installed SDK versions;
    - the Google project and location if selected;
    - `STT_LANGUAGE_CODE` (the ElevenLabs STT language), printed so a language mismatch is visible.
  - stdout gets the table: `provider | stt_provider | step | status | first_audio_ms | total_ms | commits | lag_ms | wer | detail`.
  - `detail` carries the transcript, and the metrics without their own column: `bytes=`, `audio_s=`, `recognizing=`, `keys=`. Metrics that do not apply print `-`.
  - The input is synthetic text or a file the operator chose; the README warns against pointing `--audio` at caller recordings on shared terminals.
- **`--json` mode.**
  - stdout has a `{"type":"meta",…}` object first, then one `{"type":"result",…}` per row.
  - Result fields: `provider`, `stt_provider`, `step`, `status`, `reason`, `first_audio_ms`, `total_ms`, `bytes`, `audio_s`, `commits`, `recognizing`, `lag_ms`, `wer`, `result_keys`, `transcript`. A field that does not apply is `null`, never an invented `0`.
  - All logs, provider logs included, and headers go to stderr.
- **Exit codes.**
  - `0`: at least one step ran and every step that ran passed. SKIPs are allowed. This is the general-purpose incident policy.
  - `1`: any FAIL.
  - `2`: a usage error, or every selected step was SKIP.

  A SKIP means "not validated". AC-10 (live acceptance) requires PASS for every intended combination, not exit 0.

### Not changed

- Providers, mocks, transports, `VoiceSession` and the protocols.
- Factories and dependencies: no new runtime dependency.
- CI.

## Frozen scope (judgement round 1)

| Deliverable | Surface |
|---|---|
| D-1 | `voice_runtime/speech_check.py`: parser, audio conversion, capture, dispatch through the existing factories, the step supervisor, WER, table and JSON output, exit codes. Private helpers only. |
| D-2 | `tests/test_vr014_speech_check.py`, and a test-only importable child entry point (for example `tests/vr014_child.py`) because spawn needs one. Synthetic fixtures, child-safe fakes, VR-014 markers. |
| D-3 | `README.md`, `CHANGELOG.md` and the `pyproject.toml` version; the next minor is taken from the actual baseline at enforcement. No new runtime dependency. |
| D-4 | This FR's implementation and evidence record, including the owner-approved Azure/ElevenLabs live run. Google rows are follow-up evidence, not a gate. |

## Acceptance criteria

Revised in judgement round 1. Offline criteria (AC-01 to AC-09) make no network request and use no real credentials.

- [x] **AC-01 (scaffold, RED, GREEN; R-1).**
  - A **scaffold** commit adds a minimal importable `voice_runtime/speech_check.py` (parser stub, inert `main` and the private supervisor signature with `_child_target`) and no tests.
  - A **test-only RED** commit adds `tests/test_vr014_speech_check.py` and the child entry point. Tests collect, import the scaffold normally and fail on assertions about exit codes, rows, capture, scoring or deadlines. An import, collection, fixture or pickling failure is not valid RED.
  - A **GREEN** commit implements the behaviour.
  - The three commit IDs are recorded here. Every test carries `@pytest.mark.req("VR-014")`.
- [x] **AC-02 (parsing and validation).** A table-driven test covers every §1 usage error, with exit 2 and a reason, including:
  - `--mode stt` without `--audio`; `--audio` with `--mode tts`; `--stt-provider` outside roundtrip;
  - `--timeout 0`, `-1`, `nan`, `inf`; `--max-wer -0.1`, `nan`;
  - `--max-wer` in `tts` mode and in `stt` mode without `--text`;
  - an unknown provider; `--text "  ..  "` in roundtrip and in stt.

  Valid cases: `stt` without `--text` is accepted with no reference; `--provider google --provider all` resolves to `google, azure, elevenlabs`.
- [x] **AC-03 (audio input and output).**
  - Fixtures built in the test: raw `.ulaw`, μ-law WAV and PCM16 WAV at 8 kHz mono are accepted.
  - The PCM samples `0`, `1000`, `-1000`, `32767` and `-32768` convert to the same bytes `_linear_to_ulaw` gives.
  - 16 kHz, stereo, 8-bit PCM, IEEE-float and truncated-header WAVs → exit 2.
  - `--out`: the `.ulaw` file equals the captured bytes; the `.wav` file parses as tag 7, 8000 Hz, mono, 8 bits with the same data bytes. An unwritable `--out` → that TTS row FAIL and exit 1.
- [x] **AC-04 (scoring).**
  - Distance: identical → 0.0; one substitution in five words → 0.2; one deletion → 0.2; one insertion → 0.2; case and punctuation ignored, `ä`/`ö` kept; empty hypothesis → 1.0.
  - Through status and exit code: two commits joined give the hypothesis; WER equal to `--max-wer` → PASS; above → FAIL and exit 1; `stt` without a reference → PASS with WER not applicable.
- [x] **AC-05 (steps).**
  - Capture: an emitting fake whose `speak` calls the real session's `tap_agent` → PASS with the bytes; unmodified `MockTts` → FAIL (no audio).
  - TTS FAIL, each alone: empty capture; an `error` result; `on_error` with bytes captured.
  - STT FAIL, each alone: no commits; `on_error` alongside a commit; `stop()` raises.
  - `stop()` is called after a successful `start` on normal completion, when the feed raises and when a callback raises.
  - Metrics with controlled clocks and callbacks: first-byte time, total time, bytes and audio seconds, recognizing count, commit order, `lag_ms` from the last original frame, and the settle window starting at feed completion and restarting on a later commit.
  - Roundtrip with a different `--stt-provider`: both identities in the rows; an unavailable recogniser → both rows SKIP and the fake records **no** synthesis call; a failed TTS → dependent STT row SKIP with a dependency reason and exit 1.
- [x] **AC-06 (spawn supervisor and deadlines, R-2, R-3).** Through the real spawn supervisor and the test-only child entry point; fakes report their PID and calls back through child-safe communication:
  - a small result and a result larger than the pipe buffer (≥ 1 MiB of audio) both transfer and PASS;
  - for a **single selected step** with `--timeout 2`: cooperative timeout after a successful start (`stop()` observed); a blocked synchronous `speak` after a successful start; an STT `start` that never returns; a `stop()` that blocks; and a child that ignores `terminate` (kill escalation). Each gives FAIL timeout, ends within `--timeout + 3` s, and leaves no child alive (checked by PID). `stop()` is asserted only on the cooperative path;
  - after a timed-out provider, the next selected provider still runs.
- [x] **AC-07 (SKIP and FAIL classification, R-5).**
  - Missing environment for each provider → SKIP naming the variable; a planted sentinel key value never appears in stdout or stderr.
  - A missing optional dependency (`find_spec` → `None`, simulated in the child) → SKIP naming the module; a dependency present but raising `ImportError` or `SyntaxError` on import → FAIL.
  - `google` before VR-013: `all` gives `google` SKIP (provider not in this version) while the other providers run; with a fake Google implementation installed in the child, `google` dispatches through the same path.
  - `DefaultCredentialsError` → SKIP; any other ADC exception → FAIL. A permission error after construction → FAIL.
  - Exits: `0` (one pass plus one skip), `1` (one fail), `2` (all skip).
- [x] **AC-08 (output).**
  - In `--json` mode, with a fake that writes provider logs, every non-empty stdout line parses with `json.loads` as an object, `meta` is first, and the logs are on stderr.
  - Result objects carry the §4 fields; fields that do not apply are `null`.
  - The table `detail` carries `bytes=`, `audio_s=`, `recognizing=` and `keys=`.
  - The SHA is `none` when the package is loaded from a temporary copy outside any voice_runtime checkout.
- [x] **AC-09 (matrix and artifacts; one-time record).**
  - The full suite passes in fresh Python 3.11 and 3.12 venvs with `.[dev,azure,elevenlabs]`.
  - `ruff check voice_runtime/` passes.
  - `python -m build` and `twine check` pass.
  - From outside the checkout, a clean non-editable wheel install runs `python -m voice_runtime.speech_check --help`, and `--provider azure --mode tts` with `AZURE_SPEECH_KEY` unset (→ SKIP, exit 2).
  - The commands, interpreter versions and outputs are recorded here.
- [x] **AC-10 (live, owner-approved).** Only after Q1 is answered yes, run with `STT_LANGUAGE_CODE` set explicitly to the ElevenLabs Finnish code and record the result:

  ```bash
  python -m voice_runtime.speech_check --provider azure --provider elevenlabs --mode roundtrip --out tmp/vr014
  python -m voice_runtime.speech_check --provider azure --mode roundtrip --stt-provider elevenlabs
  python -m voice_runtime.speech_check --provider elevenlabs --mode roundtrip --stt-provider azure
  ```

  - Every row must be **PASS**. A SKIP or FAIL blocks release and is recorded as failure evidence.
  - Record the full output, versions, SHA and the Azure region.
  - Once VR-013 has shipped and its spend is approved, the same matrix with `google` added in every position is recorded as a follow-up row set. It is not a gate for this FR's release.
- [x] **AC-11 (docs and record).**
  - `README.md` gains a speech-check section covering:
    - usage and an example table;
    - the default sentence, when WER applies, and the threshold rule;
    - every SKIP cause, FAIL semantics and exit codes, including "SKIP is not validation";
    - the per-step child-process timeout, and that a hard-killed step does not run `stop()`;
    - the warning about caller recordings.
  - `CHANGELOG.md` records VR-014 under the actual next minor; the version is bumped. Tagging and publishing need the owner's explicit "publish".
  - This FR records the scaffold, RED and GREEN commits, decisions and deviations, the AC-09 and AC-10 output, and the owner answer to Q1. An unanswered or declined gate is recorded as blocked, not fulfilled.

## Owner question (VR-012 R-7). Answer and record before AC-10

1. **Q1.** May the Azure subscription (CSAP `.env` key, region `swedencentral`) and the ElevenLabs account fund the AC-10 billable synthetic-only runs? An unanswered or negative answer blocks AC-10 and release, not offline work. Google rows need VR-013 Q1 or a separate approval.
   - **Answer (2026-11-16, Sami Heikkinen): approved.** AC-10 may use the CSAP `.env` Azure Speech key (`swedencentral`) and the ElevenLabs account, synthetic text only.

## Implementation record (2026-11-16)

### Commits (branch `feat/vr-014-speech-check-cli`, stacked on `feat/vr-013-google-chirp-providers`, local, unpushed)

| Step | Commit | Content |
|---|---|---|
| Scaffold | `6b34c47` | importable `speech_check.py`: parser stub, inert `main`, `_supervise(..., _child_target=None)` |
| RED | `0f18452` | 77 tests + `tests/vr014_child.py`; 77 failed, all on assertions |
| GREEN | `4bd9a62` | implementation; 77 passed, full suite 888 passed, 1 skipped |
| RED | `26b9166` | the checkout version must come from its `pyproject.toml` (see D-4) |
| GREEN | `1d1892d` | version and SHA both taken from the checkout |
| Docs | `29b3a96` | README section, CHANGELOG 0.6.0, version bump, AC-10 evidence |

### Evidence

- Offline: `tests/test_vr014_speech_check.py` 78 passed, in 5 consecutive runs; full suite 889 passed, 1 skipped (`pytest tests --no-cov`, Python 3.14 checkout venv with every extra).
- AC-09: [evidence/VR-014-ac09.md](evidence/VR-014-ac09.md): Python 3.11.14 and 3.12.13, 766 passed, 10 skipped each; ruff, build, twine pass; the wheel outside the checkout runs `--help` and SKIPs Azure with exit 2.
- AC-10: [evidence/VR-014-ac10-live.md](evidence/VR-014-ac10-live.md): 5 of 6 pairs PASS. **ElevenLabs TTS → ElevenLabs STT FAIL**, WER 0.25: the default ElevenLabs voice (`21m00Tcm4TlvDq8ikWAM`, `eleven_multilingual_v2`) said "Mä haluaisin … ens viikolla". Azure STT hears the same words from the saved audio, so the voice, not the recogniser, produced them; in another run the same voice read the sentence correctly.
  - **Owner decision (2026-11-16, Sami Heikkinen):** the FAIL is recorded and accepted as a finding about the ElevenLabs default voice, not a tool defect. It does not block release. Google rows stay a follow-up.

### Decisions and deviations made during enforcement

- **D-1 Split (deviation from "one module").** `speech_check.py` would have been 590 lines, over the 450-line gate. Parent-side pure helpers (parser, validation, audio loading, scoring, row building, header and table formatting) moved to the private `voice_runtime/_speech_check_io.py`. The child-side names the tests patch (`_find_spec`, `_adc_default`, `_feed`, `_SttCapture`, `_FRAME_S`, `_SETTLE_S`, `_child_main`) stay in `speech_check.py`, which re-exports `_wer`. Same precedent as VR-013's `_google_config.py`.
- **D-2 STT construction.** §2 says `get_stt_class(p)()`; the step calls `voice_runtime.stt.create_stt(p)`, which builds the same class with no arguments. The factory is resolved through the module attribute at call time, so the child-installed fakes take effect. Factories are unchanged.
- **D-3 Timing tests.** AC-05 asks for "controlled clocks". The tests use real time with scaled constants set inside the child (`_FRAME_S` 0 or 2 ms, `_SETTLE_S` 0.1 to 0.5 s) rather than a fake clock, because the step runs in a spawned child. Bounds are chosen to be stable: 5 consecutive runs pass.
- **D-4 Version in a checkout.** Found in the first CLI smoke run: the header said `version=0.1.12` because the checkout venv holds a stale editable install's metadata. When the package sits in a voice-runtime checkout, the version now comes from that checkout's `pyproject.toml` (and the SHA from its git); installed metadata is used only outside a checkout. Witnessed by `test_checkout_version_comes_from_its_pyproject`.
- **D-5 Row identities.** In `stt` mode, `provider` and `stt_provider` both name the recogniser. In roundtrip, when the recogniser cannot run, the TTS row is SKIP with `recogniser <name>: <reason>` and the STT row carries the recogniser's own SKIP reason (not `dependency: …`, which is kept for a failed or skipped TTS step).
- **D-6 Branch and version.** Stacked on the unreleased VR-013 branch (0.5.0), so VR-014 takes 0.6.0. The owner chose stacking.
- **D-7 Test invocation.** Run the suite as `pytest tests --no-cov`: a bare `pytest` also collects `tmp/worktrees`, and the surrounding yamlgraph `pyproject.toml` adds `--cov=yamlgraph`, which reports 0 % here.

## Out of scope

- Google providers and any factory change (VR-013).
- Changes to providers, mocks, the session, protocols or transports.
- Generalised subprocess or plugin infrastructure; any CLI option or environment variable that injects providers.
- New runtime dependencies.
- Real caller or patient recordings; reading or logging credential values.
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
- `voice_runtime/audio.py` (`_write_wav_header`: tag 7, 8000 Hz, mono)
- `scripts/probe_vr009_lid.py`; `reference/release-checklist.md`
