# VR-012 — Google Chirp 3 STT and TTS providers, and a cross-provider speech check CLI

**Priority:** MEDIUM
**Type:** Feature. It adds providers behind the existing `SttProvider`/`TtsProvider` interfaces and adds a command-line tool. The Azure, ElevenLabs and mock providers are unchanged.
**Status:** Split 2026-11-16 (judgement round 1: SPLIT, accepted by the owner). Not implemented. Its scope continues in [VR-013](VR-013-google-chirp-speech-providers.md) (Google providers) and [VR-014](VR-014-speech-check-cli.md) (speech check CLI). The text below is the round-1 proposal as judged and is kept unchanged as the record.
**Requested:** 2026-11-16
**Jira:** VBOT-212, under epic VBOT-210 ("Google Chirp as an alternative speech provider"). Related stories: VBOT-211 (enable the APIs through Terraform) and VBOT-213 (failover planning).
**Effort:** 2–3 days: two providers with fake-client tests, the CLI, and a live probe. CSAP wiring is a separate CSAP FR.
**Target release:** the next **minor** release after the release baseline at enforcement (`reference/release-checklist.md`: a new feature is MINOR). From today's unreleased 0.4.0 that is 0.5.0. If 0.4.0 has been released by then, it is still the next minor.
**First consumer:** CSAP. A later CSAP FR sets `STT_PROVIDER=google` / `TTS_PROVIDER=google` (or `STT_SECONDARY_PROVIDER=google` for shadow comparison) on the TEST worker. Until then the speech check CLI is also a consumer: it is how the owner verifies Azure, ElevenLabs and Google from one command.
**Research:** [evidence/VR-012-research.md](evidence/VR-012-research.md): `eu` region evidence, SDK surface, live streaming probe, Google limits, prior-art dispositions.
**Prior art:**
- NC-161/NC-258 and VR-005/VR-009 (`AzurePersistentStt`: one recogniser builder shared by start and reconnect, backoff, owned tasks, a commit log line without the transcript text).
- NC-260 (`on_error` contract).
- NC-199/NC-203 (`on_recognizing` drives the consumer's silence timer).
- `scripts/probe_vr009_lid.py` (probe pattern).
- CSAP NC-160 (superseded) and CSAP NC-567 (spike; its `eu` block has since lifted).
**Judgement:** [SPLIT](VR-012-google-chirp-stt-tts-providers.judgement.md), round 1, 2026-11-16, against `bee7623`. R-1 to R-7 are folded into the successors.

## Problem and ideal result

Every voice_runtime consumer depends on one speech vendor at a time. CSAP runs Azure for both speech-to-text (STT) and text-to-speech (TTS); ElevenLabs is the only alternative. An Azure Speech outage therefore silences CSAP. Switching to ElevenLabs means another external processor and another credential.

Google Chirp 3 is now reachable from CSAP's own GCP tenant in the `eu` multi-region (research §1). It sounds and transcribes well on synthetic Finnish (research §2, §4). Authentication can use the workload identity the CSAP pods already have, so no new secret is needed. voice_runtime has no Google provider, so none of this is usable without a library change.

There is also no single way to answer "do the speech providers work right now?". Today that takes three hand-written probes (curl for Google, `probe_vr009_lid.py` for Azure STT, nothing for ElevenLabs).

**Ideal result:**

1. A consumer selects `google` in `create_stt` / `get_stt_class` / `create_tts` and gets Chirp 3 STT and TTS that honour the same interface contract as Azure: callbacks, `set_speaking`, echo discard, reconnect, `on_error`, barge-in and the mark wait. No consumer code changes beyond the provider name and configuration.
2. One command, `python -m voice_runtime.speech_check`, exercises TTS, STT and a TTS→STT round trip for `azure`, `elevenlabs` and `google`. It prints a comparison table and exits non-zero on failure, so the same command works for a release check, an incident check ("is Azure down?") and later failover drills (VBOT-213).
3. Installing without the `google` extra behaves exactly as today.

## Provider facts this FR relies on

Verified in [research](evidence/VR-012-research.md) unless marked as from the docs.

1. **Endpoints.**
   - STT v2 gRPC: `eu-speech.googleapis.com`, recognizer `projects/{project}/locations/eu/recognizers/_` (the implicit recognizer; no resource has to be created).
   - TTS: `eu-texttospeech.googleapis.com`.
   - Both answered from `scp-tenant-csap-test` on 2026-11-13 and 2026-11-16.
2. **Authentication** is Application Default Credentials (ADC). A workstation using user credentials must also set a quota project (the `x-goog-user-project` header, or `ClientOptions(quota_project_id=...)`). A GKE pod with workload identity needs neither.
3. **Streaming STT accepts explicit `MULAW`, 8000 Hz, mono.** Model `chirp_3`, language `fi-FI` (GA in `eu`).
4. **Chirp 3 streaming returns no interim results.** Only final results and voice-activity events (`SPEECH_ACTIVITY_BEGIN`/`END`, with `enable_voice_activity_events=True`) arrive, although `interim_results=True` was requested (research §4, finding 1).
5. **A final result arrives 0.45–0.60 s after `SPEECH_ACTIVITY_END`** with `ENDPOINTING_SENSITIVITY_SUPERSHORT`, measured on clean synthetic speech. A turn of several sentences produces one final result per sentence. Transcripts have a leading space.
6. **Stream limits (docs):**
   - at most 25 KB of audio per request;
   - at most **5 minutes per stream**;
   - audio at about real-time pace;
   - 300 concurrent streams per region and 3,000 requests per minute per project.

   CSAP calls can run longer than 5 minutes, so the provider must restart the stream.
7. **Streaming TTS** (`streaming_synthesize`, Chirp 3 HD voices only) with `StreamingAudioConfig(MULAW, 8000)` returns **raw μ-law with no WAV header**. The first chunk arrived in 0.48 s, a 7.6 s sentence finished in 1.07 s, in 34 chunks. Non-streaming `text:synthesize` returns a WAV with a header instead (research §2).
8. **Voices.** 30 `fi-FI-Chirp3-HD-*` voices exist; `Charon` and `Achernar` were checked by ear and by STT round trip.
9. **SDK surface.** `google-cloud-speech==2.41.0` and `google-cloud-texttospeech==2.38.0` expose every type this FR uses (research §3). The lowest versions that still work are not known (AC-11).
10. **Speech adaptation (phrase biasing) is GA for Chirp 3 (docs).** One error seen in research §1 was "Terveystalo" heard as "Tervolan", which phrase hints target.

**Not verified (consumer field acceptance, in the CSAP FR):** real phone audio and real callers; Swedish/English callers; latency from the CSAP pods to `eu`; behaviour at the concurrency limit; the quality comparison against Azure on the NC-567 56-clip set; why the `eu` location policy now passes (VBOT-211).

## Proposed solution

### 1. `voice_runtime/providers/google_stt.py`: `GooglePersistentStt(SttProvider)`

Constructor:

```python
GooglePersistentStt(
    project: str | None = None,          # GOOGLE_SPEECH_PROJECT, else GOOGLE_CLOUD_PROJECT; missing → ValueError at start()
    location: str | None = None,         # GOOGLE_SPEECH_LOCATION, default "eu"
    model: str | None = None,            # GOOGLE_STT_MODEL, default "chirp_3"
    language_code: str = "fi-FI",        # GOOGLE_STT_LANGUAGE overrides when set
    phrase_hints: Sequence[str] | None = None,  # GOOGLE_STT_PHRASES (comma-separated), default none
    endpointing: str | None = None,      # GOOGLE_STT_ENDPOINTING: supershort|short|standard, default supershort
)
```

**Behaviour:**

- **Recogniser builder.** `_build_stream_config()` builds the first `StreamingRecognizeRequest`:
  - recognizer path, `ExplicitDecodingConfig(MULAW, 8000, 1)`, model and language;
  - inline `SpeechAdaptation` built from `phrase_hints`, only when hints are set;
  - `interim_results=True`, `enable_voice_activity_events=True`, the endpointing sensitivity.

  Start, stream restart and reconnect all use this one builder.
- **Client.** `SpeechAsyncClient(client_options=ClientOptions(api_endpoint=f"{location}-speech.googleapis.com", quota_project_id=project))`.
- **Audio feed.**
  - Frames read from `inbound_queue` (160-byte, 20 ms) are grouped into 100 ms (800-byte) requests, well under the 25 KB cap.
  - While `set_speaking(True)`, frames are sent as μ-law silence (`0xFF`), so the stream stays at real-time pace and does not time out.
  - Any result during speaking is discarded. Echo discard after speaking ends uses the same `ECHO_DISCARD_WINDOW_S` as Azure.
  - Frames are not dropped across a restart or reconnect: they wait in `inbound_queue`, as in NC-340.
- **Responses.**
  - A final result (`is_final=True`) with non-empty stripped text → `on_committed(text)`.
  - An interim result → `on_recognizing(text)`. Fact 4 says these do not arrive today; the branch exists so it keeps working if Google enables them.
  - `SPEECH_ACTIVITY_BEGIN` → `on_recognizing("")`. This is the only "caller is speaking" signal Chirp 3 gives (fact 4). It is fired once per speech segment, not repeatedly as Azure does (see Open risk R1).
  - `SPEECH_ACTIVITY_END` → log at debug level only.
- **Stream restart (fact 6).**
  - After 270 s, the provider restarts at the next final result.
  - The restart is forced at 290 s whatever the speech state; the forced restart is logged at warning level with `forced=true`.
  - A restart closes the request generator, waits for the response stream to finish, and opens a new stream through the same builder. It does not count as a reconnect attempt and does not call `on_error`.
- **Reconnect.**
  - Retried: `ServiceUnavailable`, `DeadlineExceeded`, `InternalServerError`, `Aborted`, and an unexpected end of the stream.
  - Backoff and attempts are as in Azure: base 1 s, at most 30 s, 3 attempts. After the attempts run out → `on_error("reconnect_exhausted_<ExceptionName>")`.
  - Terminal, no retry: `PermissionDenied`, `Unauthenticated`, `InvalidArgument`, `NotFound`, and `ResourceExhausted` (quota). These call `on_error("<exception_name_snake>")` directly. This follows ElevenLabs' split between fatal and terminal errors (NC-260 Gap C).
- **Task ownership.** Every background task is created through the provider's owned-task helper, and `stop()` cancels and awaits it (VR-005).
- **Commit log line.** `Google STT committed lang=<language_code> commit_lag_ms=<int> chars=<int>`, never the text (VR-009).
  - `commit_lag_ms` here is `SPEECH_ACTIVITY_END` → final result, or `-1` when there was no END event. This is a different measure from Azure's interim→commit; the README says so.
  - Restart: `Google STT stream restart age_s=<int> forced=<bool>`.

### 2. `voice_runtime/providers/google_tts.py`: `GoogleTTS(TtsProvider)`

Constructor:

```python
GoogleTTS(
    project: str | None = None,       # quota project only (ADC); same env fallback as STT
    location: str | None = None,      # GOOGLE_SPEECH_LOCATION, default "eu"
    voice_name: str | None = None,    # GOOGLE_TTS_VOICE, default "fi-FI-Chirp3-HD-Charon"
)
```

`speak(text, session, stop_event)` is synchronous, like the Azure and ElevenLabs providers:

- **Synthesis.** `TextToSpeechClient(...).streaming_synthesize(...)`: a config request (voice, language derived from the voice name, `StreamingAudioConfig(MULAW, 8000)`) followed by one text request.
- **Audio out.**
  - Each audio chunk is split into 160-byte frames, each passed to `session.put_outbound_sync(frame)` and `session.tap_agent(frame)`.
  - A leftover of less than 160 bytes is carried into the next chunk and padded with `0xFF` at the end.
  - Fact 7 gives raw μ-law. If a chunk still starts with `RIFF`, the provider strips the WAV header and logs that once at warning level, so a server-side format change cannot become audible noise.
- **Barge-in.** `stop_event` is checked between chunks. When it is set, the provider cancels the response stream and returns `{"interrupted": True, ...}`, with the same keys the Azure provider returns.
- **Completion.** `session.send_mark_and_wait("tts_complete", timeout=30.0)`, then `{"last_spoken": text}`.
- **Errors.**
  - A Google API error → `self.on_error("<exception_name_snake>")` and `{"error": ...}`. Same shape as `AzureTTS`.
  - A transport disconnect during the mark wait → `{"call_disconnected": True}`, as in the existing providers.

### 3. Factories and interfaces

- `stt.py`: `get_stt_class("google")` and `create_stt("google", **kw)` import `GooglePersistentStt` lazily.
- `tts.py`: `create_tts("google", **kw)` imports `GoogleTTS` lazily.
- Unknown provider names keep today's error.
- The docstrings list `google`.

### 4. Packaging and configuration

- `pyproject.toml` gains a `google` extra: `google-cloud-speech>=2.41.0` and `google-cloud-texttospeech>=2.38.0`. The floors may be lowered only through AC-11.
- `publish.yml` test job installs the `google` extra alongside `azure`, so CI runs the new tests on 3.11 and 3.12.
- Environment variables, all optional:

  | Variable | Default |
  |---|---|
  | `GOOGLE_SPEECH_PROJECT` | falls back to `GOOGLE_CLOUD_PROJECT` |
  | `GOOGLE_SPEECH_LOCATION` | `eu` |
  | `GOOGLE_STT_MODEL` | `chirp_3` |
  | `GOOGLE_STT_LANGUAGE` | `fi-FI` |
  | `GOOGLE_STT_PHRASES` | none |
  | `GOOGLE_STT_ENDPOINTING` | `supershort` |
  | `GOOGLE_TTS_VOICE` | `fi-FI-Chirp3-HD-Charon` |

  An invalid `GOOGLE_STT_ENDPOINTING` raises `ValueError` in the constructor. There are no secrets: authentication is ADC only, and the provider never reads or logs a credential file.

### 5. `voice_runtime/speech_check.py`: cross-provider speech check CLI

The CLI ships **in the package**, run as `python -m voice_runtime.speech_check`, not under `scripts/`. That way it can run inside a consumer's pod (CSAP worker, black-box tester) using that pod's own credentials. That is the place where an outage has to be confirmed. There is no console-script entry point.

```text
python -m voice_runtime.speech_check
    --provider azure|elevenlabs|google|all     (repeatable; default all)
    --mode tts|stt|roundtrip                   (default roundtrip)
    --text "…"                                 (default: built-in Finnish sentence, synthetic)
    --audio PATH                               (stt mode: .ulaw raw 8 kHz, or .wav μ-law/PCM 8 kHz mono)
    --stt-provider NAME                        (roundtrip: recogniser for every TTS output; default = same provider)
    --out DIR                                  (write <provider>.ulaw and <provider>.wav of each TTS output)
    --timeout SECONDS                          (per provider step; default 30)
    --json                                     (machine-readable result lines instead of the table)
```

**Mechanics.** The CLI uses only public voice_runtime API:

- **TTS step.**
  - Build a real `VoiceSession` with no loop attached, so `put_outbound_sync` and `send_mark_and_wait` return immediately.
  - Attach a capture mixer through `session.set_mixer(...)`; it implements `write_agent`/`write_caller` and appends agent bytes.
  - Call `create_tts(p).speak(text, session, threading.Event())` and record:
    - time to the first captured chunk;
    - total time;
    - captured bytes and their audio length;
    - the returned dict.
  - Fail if no audio was captured, the result has an `error` key, or `on_error` fired.
- **STT step.**
  - Construct `get_stt_class(p)()` and wire `on_committed`, `on_recognizing` and `on_error` into recorders.
  - `await stt.start(queue)`. Feed 160-byte frames every 20 ms (real-time pace, fact 6), then 3 s of `0xFF` silence. Wait up to `--timeout` for the result to settle (no new commit for 2 s), then `await stt.stop()`.
  - Record committed texts, the count of `recognizing` events, the time from end of speech to the last commit, and errors.
  - Fail if nothing was committed or `on_error` fired.
- **Roundtrip.** The TTS step's μ-law output becomes the STT step's input.
  - Score: word error rate between `--text` and the joined commits, after lowercasing, stripping punctuation and collapsing spaces. It is a simple Levenshtein distance over words with no extra dependency.
  - Number normalisation is out of scope, so the default sentence has no numbers.
  - Pass if WER ≤ `--max-wer` (default 0.2).
- **Missing configuration.** Missing credentials or a missing extra for a selected provider skips it with status `SKIP <reason>`; the reason never includes a key value. Examples: `AZURE_SPEECH_KEY` unset, `ELEVENLABS_API_KEY` unset, no ADC or project, `ImportError` of the extra. A run where *every* selected provider is `SKIP` exits 2.
- **Output.**
  - Header: voice_runtime version, git SHA if run from a checkout, SDK versions present, and the Google project and location in use.
  - One table row per provider and step: `provider | step | status | first_audio_ms | total_ms | commits | lag_ms | wer | detail`.
  - Transcripts are printed, because the input is synthetic (`--text` or a file the operator chose). The README warns not to point `--audio` at caller recordings on shared terminals.
- **Exit codes.** `0` all selected providers pass (SKIPs allowed if at least one ran); `1` any FAIL; `2` usage error or nothing runnable.
- **ElevenLabs STT.** `PersistentSttSession` uses `STT_LANGUAGE_CODE` (default `en`). The CLI sets nothing implicitly; it prints the language each provider is using in the header so a mismatch is visible.

### Not changed

- `AzurePersistentStt`, `AzureTTS`, `PersistentSttSession`, `ElevenLabsTTS`, the mock providers and the transports.
- `SttProvider`/`TtsProvider` signatures and the `VoiceSession` API.
- Default provider names.
- Existing environment variables.
- Installs without the `google` extra.

## Acceptance criteria

- [ ] **AC-01 (RED).** A test-only commit adds `tests/test_vr012_google_stt.py`, `tests/test_vr012_google_tts.py` and `tests/test_vr012_speech_check.py`.
  - Failures are assertions or `ImportError` of the not-yet-existing provider module, caught inside each test. They are not collection errors.
  - Every new test carries `@pytest.mark.req("VR-012")`.
  - The Google client classes are faked; no test opens a network connection.
- [ ] **AC-02 (STT config).** With a fake `SpeechAsyncClient`, the first request has:
  - recognizer `projects/p1/locations/eu/recognizers/_`;
  - `ExplicitDecodingConfig` with `MULAW`, 8000 Hz, 1 channel;
  - model `chirp_3` and `language_codes == ["fi-FI"]`;
  - `interim_results`, `enable_voice_activity_events`, and `ENDPOINTING_SENSITIVITY_SUPERSHORT`;
  - no adaptation.

  With `phrase_hints=["Terveystalo"]`, the inline adaptation contains exactly that phrase.

  The client's `api_endpoint` is `eu-speech.googleapis.com`. Environment fallbacks are tested with `monkeypatch`:
  - `GOOGLE_SPEECH_PROJECT` wins over `GOOGLE_CLOUD_PROJECT`;
  - neither set → `ValueError` at `start()`;
  - `GOOGLE_STT_ENDPOINTING=bogus` → `ValueError` in the constructor.
- [ ] **AC-03 (STT audio).** Twenty-five 160-byte frames reach the fake as five 800-byte audio requests in order. Frames received while `set_speaking(True)` are sent as `0xFF` of the same length.
- [ ] **AC-04 (STT callbacks).**
  - A final result `" Hei."` → `on_committed("Hei.")` exactly once.
  - A final result of whitespace only → no call.
  - An interim result → `on_recognizing(text)`.
  - `SPEECH_ACTIVITY_BEGIN` → `on_recognizing("")`.
  - A final result while speaking, or within the echo window after speaking ends (patched `time.monotonic`) → no `on_committed`.
  - The commit log line matches `Google STT committed lang=fi-FI commit_lag_ms=<int> chars=4`, with the exact lag from a patched clock after an END event and `-1` without one. The record never contains the transcript text.
- [ ] **AC-05 (stream restart).** With a patched clock:
  - a final result after 270 s triggers a restart;
  - no final result by 290 s forces one, logged with `forced=true`.

  The second stream's first request equals the first stream's, as built by the same builder. Frames queued during the restart arrive on the new stream with none lost or duplicated (counted by byte). `on_error` is not called.
- [ ] **AC-06 (STT errors).**
  - `ServiceUnavailable` → reconnect with backoff, and the new stream has the same first request.
  - Three consecutive failures → `on_error("reconnect_exhausted_ServiceUnavailable")`.
  - Each of `PermissionDenied`, `Unauthenticated`, `InvalidArgument`, `NotFound` and `ResourceExhausted` → `on_error` once with no reconnect attempt.
  - After `stop()`, no owned task is still pending (VR-005 assertion style).
- [ ] **AC-07 (TTS).** With a fake `TextToSpeechClient` yielding raw μ-law chunks of 333, 160 and 7 bytes:
  - the first request carries voice `fi-FI-Chirp3-HD-Charon` and `StreamingAudioConfig(MULAW, 8000)`;
  - `put_outbound_sync` and `tap_agent` receive 160-byte frames whose concatenation equals the input padded with `0xFF` to a multiple of 160;
  - `send_mark_and_wait("tts_complete", timeout=30.0)` is called once;
  - the result has `last_spoken`.

  Further cases:
  - A chunk starting with a `RIFF` header is stripped, with one warning.
  - `stop_event` set after the first chunk → the stream is cancelled, `interrupted` is returned and no mark is sent.
  - A fake `PermissionDenied` → `on_error` is called and `error` is returned.
- [ ] **AC-08 (factories and isolation).**
  - `get_stt_class("google") is GooglePersistentStt`, `create_stt("google", project="p")` and `create_tts("google", project="p")` return the new types.
  - The interface-conformance tests (NC-165) include both.
  - With the `google.cloud` import blocked (a `sys.modules` stub raising `ImportError`), `import voice_runtime`, `create_stt("azure")` and `create_tts("azure")` still work.
  - Existing tests pass without weakened assertions.
- [ ] **AC-09 (CLI, offline).** `tests/test_vr012_speech_check.py` covers:
  - argument parsing, including exit 2 on bad arguments;
  - the WER function: identical text → 0.0; one substitution in five words → 0.2; case and punctuation ignored;
  - the capture mixer and the loop-less TTS step against `MockTts` (`voice_runtime/mock/tts.py`) or a fake;
  - the STT step against `MockStt` from `voice_runtime/mock/stt.py`, or a fake if no mock fits;
  - SKIP when credentials are missing, without the key value in the output;
  - exit codes 0/1/2 for pass/fail/all-skipped;
  - `--json` lines parse as JSON.
- [ ] **AC-10 (live, owner-approved).** One recorded run of the CLI against real services, with synthetic text only:

  ```bash
  python -m voice_runtime.speech_check --provider all --mode roundtrip --out tmp/vr012
  python -m voice_runtime.speech_check --provider azure --provider elevenlabs --provider google \
      --mode roundtrip --stt-provider google
  ```

  It runs in `scp-tenant-csap-test` with Azure `swedencentral` and the ElevenLabs keys from the CSAP `.env`. **Owner approval is required before the run** to use those accounts for synthetic speech. The FR records:
  - the full table output, versions and SHA;
  - the ElevenLabs STT language used;
  - one Google STT session kept open for more than 5 minutes (looped synthetic audio with silence gaps), showing at least one `stream restart` line and commits continuing after it.

  A Google 403, 404 or region error, or a lost commit after a restart, blocks release. It is recorded as failure evidence, not as acceptance.
- [ ] **AC-11 (SDK floor).** In a clean venv with exactly `google-cloud-speech==2.41.0` and `google-cloud-texttospeech==2.38.0`, the SDK-surface probe (research §3) and AC-02 to AC-08 pass; the command and output are recorded here. A lower floor is set only if the same probe and tests pass at that exact version.
- [ ] **AC-12 (artifacts).**
  - Full offline suite green on Python 3.11 (CI floor) with `.[dev,azure,elevenlabs,google]`, and again with `.[dev,azure,elevenlabs]` (AC-08 isolation proven by the real absence of the extra).
  - `python -m build` and `twine check` pass.
  - A clean non-editable wheel install with `[google]` constructs both providers, and `python -m voice_runtime.speech_check --help` runs from `site-packages`.
- [ ] **AC-13 (docs).**
  - `README.md` gains a Google section covering:
    - the extra and authentication (ADC, quota project for user credentials, workload identity in pods);
    - environment variables and defaults;
    - the facts that change consumer behaviour: no interim results (`on_recognizing` fires once per speech segment), the 5-minute restart, a different meaning of `commit_lag_ms`, and that streaming TTS works only with Chirp 3 HD voices.
  - It also gains a speech check CLI section with an example table and the exit codes.
  - `.env.example` gains commented Google variables.
  - `CHANGELOG.md` records VR-012 under the next minor version, and version metadata is bumped. Tagging and publishing need the owner's explicit "publish".
- [ ] **AC-14 (record).** This FR records decisions, RED/GREEN commits, suite results, AC-10 and AC-11 output, and any deviation.

## Open risks (for the judge and the consumer FR)

- **R1. Silence-timer semantics.** CSAP resets its silence and liveness timers on every `recognizing` event (NC-203, NC-259). Azure sends many such events during an utterance; Chirp sends one `SPEECH_ACTIVITY_BEGIN` per segment.
  - Effect: a caller who keeps talking past CSAP's silence timeout without a pause could be cut off.
  - The library reports the provider truthfully and does not invent periodic events.
  - CSAP has to decide whether `SPEECH_ACTIVITY_END` should become a consumer signal, or whether to change the timer. That would need a new `on_speech_end` callback, which is a separate interface change and **out of scope** here.
- **R2. Restart gap.** A restart at the 290 s forced point can cut an utterance in half. The two halves arrive as two commits, which CSAP's FSM accumulation tolerates. Words in flight at the cut may still be lost; AC-10 measures it.
- **R3. Region policy.** If `constraints/gcp.resourceLocations` blocks `eu` again (it did on 2026-10-29), every Google call fails with a terminal error.
  - The provider reports it through `on_error`, and the CLI shows it as FAIL.
  - Settling the policy belongs to VBOT-211.
- **R4. Latency to `eu`.** The 0.5 s END→final time and the 0.48 s first TTS chunk were measured from a workstation in Finland. Latency from the CSAP pods is the consumer FR's measurement.

## Research and alternatives

| Option | Disposition |
|---|---|
| Non-streaming `Recognize` per utterance (local voice activity detection, then a batch call) | Rejected. voice_runtime removed per-turn STT in NC-166. It would need a local VAD, and it adds the full utterance length to the delay. |
| REST via `httpx` instead of the Google SDKs | Rejected. STT streaming is gRPC only, so the SDK is needed anyway. One dependency family, ADC handled. |
| Non-streaming `text:synthesize` for TTS | Kept as a fallback only (the WAV-header strip). Streaming gives the first audio in 0.48 s against about 0.9 s for the whole sentence. |
| CLI under `scripts/` (like `probe_vr009_lid.py`) | Rejected. It would not ship in the wheel, so it could not run in a pod during an incident. |
| Synthesising periodic `on_recognizing` during active speech | Rejected (R1). It would invent provider behaviour; a consumer decision belongs in CSAP. |
| Chirp 2 / `long` model | Out of scope. Chirp 3 is GA for `fi-FI` in `eu` and was verified. |

**Is this a graph?** No. These are providers and a diagnostic CLI; there is no LLM step.

## Out of scope

- CSAP wiring, the quality comparison against Azure (NC-567 clips), and field acceptance: a separate CSAP FR.
- Terraform enablement of the Speech and TTS APIs in stg and prod, the IAM roles for the workload identity, and documenting the `eu` policy change: VBOT-211.
- Automatic failover between providers: VBOT-213. The CLI is its manual building block.
- Language identification (VR-009-style multi-locale) for Google.
- An `on_speech_end` callback (R1).

## Source references

- `voice_runtime/providers/__init__.py` (interfaces), `voice_runtime/stt.py`, `voice_runtime/tts.py` (factories)
- `voice_runtime/providers/azure_stt.py` (template: `_build_recognizer`, reconnect, echo discard, commit log)
- `voice_runtime/providers/elevenlabs_stt.py` (fatal/terminal split, `session_time_limit_exceeded`)
- `voice_runtime/providers/azure_tts.py`, `voice_runtime/providers/elevenlabs_tts.py` (`speak` contract)
- `voice_runtime/session.py`:
  - `put_outbound_sync` and `send_mark_and_wait` return immediately when there is no loop, which the CLI relies on;
  - `set_mixer` and `tap_agent` are the capture hook.
- `scripts/probe_vr009_lid.py` (probe pattern)
- CSAP `services/stt.py` (`on_recognizing` → `recognizing` event) and `config/voice_coordinator_navigator.yaml` (`recognizing` resets the timers)
