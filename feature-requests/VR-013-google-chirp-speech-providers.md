# VR-013 — Google Chirp 3 STT and TTS providers

**Priority:** MEDIUM
**Type:** Feature. It adds providers behind the existing `SttProvider`/`TtsProvider` protocols. Existing providers, transports, the session and the protocols are unchanged.
**Status:** Proposed (not judged).
**Requested:** 2026-11-16
**Jira:** VBOT-212, under epic VBOT-210.
**Parent:** [VR-012](VR-012-google-chirp-stt-tts-providers.md), split by its [round-1 judgement](VR-012-google-chirp-stt-tts-providers.judgement.md). This FR owns the D-2 and D-5 surfaces and folds R-2, R-3, R-4, R-6 and R-7. The cross-provider CLI is [VR-014](VR-014-speech-check-cli.md); this FR does not depend on it.
**Effort:** 2–2.5 days: two providers, fake-client tests, two probe scripts and the live probe.
**Target release:** the next **minor** release after the actual release baseline at enforcement (`reference/release-checklist.md`). From today's unreleased 0.4.0 that is 0.5.0. If VR-014 releases first, it is the minor after that.
**First consumer:** CSAP, through a separate CSAP FR. CSAP rollout stays blocked on that FR's timer decision and field acceptance (R1 below), not on this library.
**Research:** [evidence/VR-012-research.md](evidence/VR-012-research.md): region, SDK surface, streaming behaviour, Google limits, and the probe sources as run (§7).
**Prior art:** NC-161, NC-258 and NC-340 (Azure persistent STT, reconnect, feed survives reconnect); VR-005 (owned tasks); VR-009 (commit log line without text); NC-260 (`on_error` contract); NC-199/NC-203 (`on_recognizing`); `scripts/probe_vr009_lid.py` (probe pattern).
**Judgement:** none yet.

## Problem and ideal result

Every voice_runtime consumer depends on one speech vendor at a time. For CSAP an Azure Speech outage silences the bot, and ElevenLabs is the only alternative. Google Chirp 3 works from CSAP's own GCP tenant in the `eu` multi-region (research §1, §4), but voice_runtime has no Google provider.

**Ideal result:** `get_stt_class("google")`, `create_stt("google")` and `create_tts("google")` return Chirp 3 providers that conform to the existing protocols.

**What this does not promise.** Conformance to the interface is not the same callback timing as Azure. Chirp 3 sends no interim results, so `on_recognizing` fires once per speech segment, not many times per utterance (fact 4). A consumer whose timers depend on that timing must decide for itself whether it can switch (R1). Installing without the `google` extra behaves exactly as today.

## Provider facts this FR relies on

Verified in [research](evidence/VR-012-research.md) unless marked as from the docs.

1. **Endpoints.**
   - STT v2 gRPC `eu-speech.googleapis.com`, with the implicit recognizer `projects/{project}/locations/eu/recognizers/_`.
   - TTS `eu-texttospeech.googleapis.com`.
2. **Authentication** is Application Default Credentials (ADC). User credentials also need a quota project (`ClientOptions(quota_project_id=...)`).
3. **STT input.** Streaming STT accepts explicit `MULAW` / 8000 Hz / mono, model `chirp_3`, `fi-FI` (GA in `eu`).
4. **No interim results.** Chirp 3 streaming returns no interim results, only final results plus `SPEECH_ACTIVITY_BEGIN`/`END` events (with `enable_voice_activity_events`). Evidence: research §4, synchronous client.
5. **Final-result timing.** With `SUPERSHORT` endpointing, a final result arrives 0.45–0.60 s after `SPEECH_ACTIVITY_END`. There is one final result per sentence, and transcripts carry a leading space.
6. **Stream limits (docs).** At most 25 KB of audio per request, at most 5 minutes per stream, and audio at about real-time pace. Quotas: 300 concurrent streams per region and 3,000 requests per minute.
7. **TTS output.** Streaming TTS (Chirp 3 HD voices only) with `StreamingAudioConfig(MULAW, 8000)` returns raw μ-law. The first chunk arrived in 0.48 s.
8. **SDK.** `google-cloud-speech==2.41.0` and `google-cloud-texttospeech==2.38.0` expose every type used here, on Python 3.14 only (research §3). The CI floors 3.11 and 3.12 are unproven until AC-10.

## Proposed solution

### 1. `voice_runtime/providers/google_stt.py`: `GooglePersistentStt`

**Constructor.** Every argument is optional. When an argument is not given, the provider uses the environment variable below or its default:

| Argument | Environment variable | Default |
|---|---|---|
| `project` | `GOOGLE_SPEECH_PROJECT`, then `GOOGLE_CLOUD_PROJECT` | none: missing → `ValueError` at `start()` |
| `location` | `GOOGLE_SPEECH_LOCATION` | `eu` |
| `model` | `GOOGLE_STT_MODEL` | `chirp_3` |
| `language_code` | `GOOGLE_STT_LANGUAGE` | `fi-FI` |
| `phrase_hints` | `GOOGLE_STT_PHRASES` (comma-separated) | none |
| `endpointing` | `GOOGLE_STT_ENDPOINTING` | `supershort`; other values `short`, `standard`; anything else → `ValueError` in the constructor |

**Config.** `_build_config_request()` is the only place that builds the first request of a stream. It holds:

- the recognizer path;
- `ExplicitDecodingConfig(MULAW, 8000, 1)`;
- the model and `language_codes=[language_code]`;
- inline adaptation, only when hints are set;
- `interim_results=True`, `enable_voice_activity_events=True` and the endpointing setting.

The client is `SpeechAsyncClient(client_options=ClientOptions(api_endpoint=f"{location}-speech.googleapis.com", quota_project_id=project))`.

**Callbacks.**

- A final result whose stripped text is non-empty, outside the speaking and echo windows → `on_committed(text)`.
- An interim result → `on_recognizing(text)`.
- `SPEECH_ACTIVITY_BEGIN` → `on_recognizing("")`.
- `set_speaking(True)` suppresses commits. When speaking stops, the echo window `ECHO_DISCARD_WINDOW_S` starts, as in Azure.
- Frames that arrive while speaking are sent as `0xFF` bytes of the same length. Real-time pace is kept, and the bot's own audio is not sent to Google.

**Commit log line.** `Google STT committed lang=<code> commit_lag_ms=<int> chars=<int>`; the transcript text never appears in logs.

- `commit_lag_ms` is the time from the last `SPEECH_ACTIVITY_END` to the final result, or `-1` when there was none.
- The END timestamp is used up and cleared by **every** final result, including empty or discarded ones, and is cleared when a stream is replaced (R-3).

**Session owner (R-3).** One owned task, `_run()`, created through the VR-005 owned-task pattern, is the only code that opens, rotates, reconnects or closes streams. Rotation and reconnect therefore cannot overlap.

Each stream works like this:

1. The request generator yields the config request, then 800-byte (100 ms) audio requests taken from a provider-owned carry buffer. The carry buffer is filled from `inbound_queue`.
2. **Pacing.** Requests are sent at real-time pace (one 800-byte request per 100 ms of wall clock) while the carry buffer holds less than 800 bytes of backlog. A larger backlog (for example after a reconnect) is sent at no more than **2× real time** until it has caught up. Decision D-1 explains why.
3. Bytes leave the carry buffer only when the generator yields them. Bytes not yet yielded, including a part-filled batch of 1–4 frames, survive rotation and reconnect.
4. When the input sentinel `None` arrives (normal end of input), the generator yields any final short batch and ends. The provider drains the stream and exits without reconnecting.

**Rotation.** `_run()` measures stream age with `time.monotonic()` and a timer that does not depend on receiving any response.

- **Ordinary rotation:** the first final result after the stream is 270 s old.
- **Forced rotation:** at 290 s, even if no response or frame has arrived. It is logged at warning level with `forced=true`.
- **Procedure:**
  1. Stop yielding from the generator, which ends the request stream.
  2. Process remaining responses normally for at most `_DRAIN_TIMEOUT_S = 5.0` s.
  3. Cancel the call.
  4. Clear the END timestamp.
  5. Open a new stream from `_build_config_request()`.
- A rotation does not count as a reconnect and does not call `on_error`.
- If opening the replacement stream fails, the retry policy below applies; that is not a rotation.
- Log line: `Google STT stream rotate age_s=<int> forced=<bool> drained=<bool>`.

**Retry policy.**

| Class | Errors | Action |
|---|---|---|
| Retryable | `ServiceUnavailable`, `DeadlineExceeded`, `InternalServerError`, `Aborted`, or the response stream ending without our rotation (unexpected EOF) | Reconnect after an `Azure`-style backoff: base 1 s, at most 30 s. |
| Terminal | `PermissionDenied`, `Unauthenticated`, `InvalidArgument`, `NotFound`, `ResourceExhausted` | `on_error("google_stt_<snake_name>")` once, then the session ends. |

- Retryable errors are handled the same way whether they happen when the stream is opened or while it is being read.
- **Attempt counting.** Up to **3** reconnect attempts after the initial failure. The counter resets only after a replacement stream succeeds, meaning it has delivered a response or has run 5 s without error. After the third failed attempt → `on_error("reconnect_exhausted_<ExceptionName>")` once, and the session ends.
- The terminal callback fires at most once per provider instance.

**`stop()`.**

- Sets a stopped flag that every transition checks. Stop is honoured during backoff, drain and opening, and nothing restarts after it.
- Cancels and awaits the owned tasks (VR-005).
- Closes the client transport (`await client.transport.close()`).

**No-loss claim (R-3).** The provider guarantees only that **locally kept audio that was never handed to the SDK** is sent once to the next stream, in order. Audio already handed to a stream that later failed was not acknowledged and may be lost. A sentence cut by a forced rotation may lose words or never be committed. AC-08 measures this, and C-2 below decides whether the release accepts it.

### 2. `voice_runtime/providers/google_tts.py`: `GoogleTTS`

**Constructor.** `project`, `location` and `voice_name` are optional; when not given, the same environment fallbacks as STT apply. `voice_name` falls back to `GOOGLE_TTS_VOICE`, default `fi-FI-Chirp3-HD-Charon`. The language code is the first two parts of the voice name (`fi-FI`).

**`speak(text, session, stop_event=None)`.** Result keys are the same as in `AzureTTS` and `ElevenLabsTTS`:

| Case | Behaviour | Result |
|---|---|---|
| `text` empty or whitespace only | no request | `{"last_spoken": ""}` |
| `session.is_disconnected` before start | no request | `{"last_spoken": "", "call_disconnected": True}` |
| `stop_event` already set | no request | `{"last_spoken": text, "interrupted": True}` |
| normal | `streaming_synthesize`: a config request (voice, `StreamingAudioConfig(MULAW, 8000)`), then one text request. Each chunk is cut into 160-byte frames → `put_outbound_sync` + `tap_agent`. A partial tail carries over to the next chunk. At normal completion only, the final partial frame is padded with `0xFF`. Then `send_mark_and_wait("tts_complete", timeout=30.0)`. | `{"last_spoken": text}` |
| stop_event set mid-stream | Cancel the call. Returns within **1 s** of the event, even if the SDK read is blocked. No more frames, no padding, no mark. | `{"last_spoken": text, "interrupted": True}` |
| session disconnected mid-stream | Cancel the call; no mark. | `{"last_spoken": text, "call_disconnected": True}` |
| Google API error | `on_error("google_tts_<snake_name>")` | `{"last_spoken": text, "error": str(exc)}` |
| mark `TimeoutError` | Warning log, as in the existing providers. | `{"last_spoken": text}` |
| disconnected after mark wait | `send_mark_and_wait` returns without error when the call drops, so `is_disconnected` is checked afterwards. | `{"last_spoken": text, "call_disconnected": True}` |

- **Reading without blocking `speak`.** The response stream is read by one worker thread owned by the call. It passes chunks through a `queue.Queue`. `speak` polls that queue every ≤50 ms and checks `stop_event` and `is_disconnected` between polls.
- **Cancelling.** Cancel calls the streaming call's `cancel()`. The worker is joined for at most 1 s, and `speak` never returns while its worker can still emit audio.
- **Raw bytes kept as-is.** Nothing is sniffed or stripped. Chunk bytes are passed through unchanged (R-4); there is no fallback to non-streaming synthesis.

### 3. Factories, packaging, configuration

- `stt.py`: `get_stt_class("google")` and `create_stt("google", **kw)`. `tts.py`: `create_tts("google", **kw)`. All three use lazy imports, and docstrings list `google`. Unknown names keep today's `ValueError`.
- `pyproject.toml` gains `google = ["google-cloud-speech>=2.41.0", "google-cloud-texttospeech>=2.38.0"]`. The floors are lowered only through AC-10.
- **D-5 (owner question Q3 must be answered first; conditional):** `.github/workflows/publish.yml`'s test-job install line becomes `".[dev,elevenlabs,azure,google]"`. No other workflow change.
- Authentication is ADC only. No credential is read, stored or logged by the provider.

### 4. Probe scripts (R-6), outside the package

- `scripts/probe_vr013_sdk_surface.py`: the research §7.1 probe, extended to every SDK name the providers use. It prints versions and exits non-zero if a name is missing.
- `scripts/probe_vr013_google_session.py` drives the **real** `GooglePersistentStt` (async client) and `GoogleTTS`:
  1. It synthesises numbered phrases `"Lause <n>. <fixed Finnish sentence>"` with `GoogleTTS`, using a capture session without a loop, exactly as `probe_vr009_lid.py` does.
  2. It feeds them in a loop with 1.5 s silence gaps at 20 ms per frame for 330 s.
  3. Ordinary rotation: during one run, a phrase is placed so that it ends just after 270 s.
  4. Forced rotation: a second run puts continuous speech (gap-free phrases) across 290 s.
  5. It prints the expected phrase-number sequence, the committed phrase numbers with timestamps, the rotate lines, and the missing or duplicated numbers.
  6. Synthetic text only; ADC with an explicit `--project`.

### Not changed

- Existing providers and transports.
- `VoiceSession`.
- The `SttProvider`/`TtsProvider` protocols.
- Default provider names and existing environment variables.
- Installs without the `google` extra.

There is no new callback and no synthetic periodic `recognizing`.

## Acceptance criteria

- [ ] **AC-01 (behavioural RED, R-2).** A test-only commit adds `tests/test_vr013_google_stt.py` and `tests/test_vr013_google_tts.py`, run with the exact SDK floors installed.
  - Tests reach the providers only through the existing factories (`get_stt_class`, `create_stt`, `create_tts`) and never import `voice_runtime.providers.google_*` at module level.
  - In RED, the only allowed failures are the factories' existing `ValueError("Unknown … provider: google")` and assertion failures. A collection, fixture or SDK import error makes the RED invalid.
  - Every test carries `@pytest.mark.req("VR-013")`.
  - The SDK client classes are faked; requests, responses and enums are the real SDK message types.
- [ ] **AC-02 (optional SDK, R-2).**
  - SDK-dependent test modules start with `pytest.importorskip("google.cloud.speech_v2", reason="optional google extra not installed")` (and the matching TTS module). No other skip is allowed.
  - A separate test asserts that importing `voice_runtime`, `voice_runtime.stt` and `voice_runtime.tts` and creating `azure` providers loads no `google.cloud` module. It runs whether or not the extra is installed.
- [ ] **AC-03 (STT config).**
  - The first request contains everything in §1 Config. Adaptation appears only with hints, and `["Terveystalo"]` produces exactly that phrase.
  - The endpoint is `eu-speech.googleapis.com`.
  - `GOOGLE_SPEECH_PROJECT` wins over `GOOGLE_CLOUD_PROJECT`.
  - No project → `ValueError` at `start()`; a bad endpointing value → `ValueError` in the constructor.
- [ ] **AC-04 (STT audio and pacing).** With a fake clock:
  - 25 frames become five 800-byte requests, in order.
  - While speaking, the bytes are `0xFF`.
  - At end of input after 3 frames, a final 480-byte request is sent.
  - A backlog of 40 frames is sent at no more than 2× real time, then at real-time pace.
- [ ] **AC-05 (STT callbacks and lag).**
  - Final `" Hei."` → `on_committed("Hei.")` exactly once; a whitespace-only final → no call.
  - An interim result → `on_recognizing(text)`; BEGIN → `on_recognizing("")`.
  - Speaking and echo-window discards work as in Azure.
  - Log line format: exact lag after END, `-1` without END. An END followed by an empty final, then a later final with no new END, logs `-1`. An END before a rotation, followed by a final on the new stream, logs `-1`.
  - The transcript text never appears in a log record.
- [ ] **AC-06 (rotation, R-3).** With a fake clock and fake streams, assert the actual stream sequence and the bytes kept:
  - ordinary rotation at the first final after 270 s;
  - forced rotation at 290 s with a **silent server** (no responses, no frames);
  - a blocked old-stream drain is cancelled after 5 s;
  - a 1–4-frame partial batch and the queued frames reach the next stream once, in order;
  - a retryable error during the drain or the replacement open is handled by the retry policy, and no second stream opens at the same time (the fake records how many streams are open at once, always ≤1);
  - `stop()` during the drain, the opening, and the backoff each leave no owned task pending and open no further stream;
  - the client transport is closed;
  - `on_error` is never called by a rotation.
- [ ] **AC-07 (retry policy).**
  - Each retryable class and an unexpected EOF, both at open and while reading, → reconnect with the same config request.
  - Three consecutive failures → exactly one `on_error("reconnect_exhausted_<Name>")`.
  - A success (a response, or 5 s without error) resets the counter: fail, succeed, fail, fail, fail → `on_error` only after the last.
  - Each terminal class → one `on_error`, no reconnect.
- [ ] **AC-08 (TTS).** With a fake client yielding raw chunks of 333, 160 and 7 bytes, including `RIFF` and `0xFF` byte values inside the payload:
  - the frames concatenate to the input with only end padding;
  - the config request is as in §2;
  - the mark is sent once.

  Every other row of the §2 table has its own test, including:
  - `stop_event=None`;
  - a fake whose next read **blocks until cancelled**: stop set → `speak` returns within 1 s, no frame or mark afterwards, and the worker thread has ended;
  - disconnect after the mark wait → `call_disconnected`.
- [ ] **AC-09 (factories).**
  - `get_stt_class("google")`, `create_stt("google", project="p")` and `create_tts("google", project="p")` return the new types.
  - The NC-165 protocol-conformance tests include them.
  - Existing tests pass without weakened assertions.
- [ ] **AC-10 (SDK floor and Python versions, R-6).**
  - Fresh venvs on **Python 3.11 and 3.12**, with exactly `google-cloud-speech==2.41.0` and `google-cloud-texttospeech==2.38.0`, run `scripts/probe_vr013_sdk_surface.py` and AC-03 to AC-09.
  - In a second fresh venv per version with `.[dev,azure,elevenlabs]` only (Google never installed), the full suite passes and the Google tests report the AC-02 skip.
  - Commands and outputs are recorded here.
  - A lower floor is allowed only if this same evidence passes at that exact version.
- [ ] **AC-11 (artifacts).**
  - `ruff check voice_runtime/` (release checklist) passes.
  - `python -m build` and `twine check` pass.
  - A clean non-editable wheel install with `[google]`, run outside the repo, builds both providers through the factories.
- [ ] **AC-12 (live, owner-approved, R-6).** Only after Q1 is answered yes, run `scripts/probe_vr013_google_session.py` against `scp-tenant-csap-test` (ordinary-rotation and forced-rotation runs) and record here:
  - versions, git SHA and the exact command;
  - the expected and committed phrase-number sequences, with rotate lines;
  - TTS first-chunk time.

  The following block release and are recorded as failure evidence:
  - a 403, 404 or region error;
  - any missing or duplicated phrase at an ordinary rotation;
  - forced-rotation loss beyond what the owner accepts under Q2.
- [ ] **AC-13 (docs).**
  - `README.md` gains a Google STT and TTS section covering:
    - the extra, ADC and quota project (workload identity in pods);
    - the environment table;
    - **interface conformance only**: `on_recognizing` cadence differs from Azure (fact 4);
    - the rotation limits and possible loss at a forced rotation;
    - the meaning of `commit_lag_ms`;
    - the restriction to Chirp 3 HD voices.
  - `.env.example` gains commented Google variables.
  - `CHANGELOG.md` records VR-013 under the actual next minor, and the version is bumped. Tagging and publishing need the owner's explicit "publish".
- [ ] **AC-14 (record).** This FR records RED/GREEN commits, decisions, suite results, AC-10 to AC-12 output, the owner answers to Q1–Q3, and deviations.

## Decisions

- **D-1 Catch-up rate.** Strict real-time pacing after a reconnect would keep the outage-length delay (up to ~7 s of backoff plus the 5 s drain) for the rest of the call. Bursting conflicts with fact 6. A capped 2× catch-up is the smallest rule that both clears the backlog and stays near real time. The judge may set it to 1×. The README then states the permanent delay.
- **D-2 Drain before open.** Following the judgement, the old stream is closed before the new one opens. There are no overlapping streams and no replay or deduplication.

## Owner questions (R-7). Answer and record before the affected action

1. **Q1.** May `scp-tenant-csap-test` be used for the AC-12 billable synthetic-only probes? *(blocks AC-12)*
2. **Q2.** Is the forced-rotation loss measured in AC-12 acceptable for a library release, while CSAP deployment remains separately gated? *(blocks release)*
3. **Q3.** Do you approve the one-line `publish.yml` install change in §3, after reviewing its exact diff? *(blocks D-5)*

## Open risks

- **R1. Consumer timers.** CSAP resets its silence and liveness timers on `recognizing` (NC-203, NC-259). Chirp's once-per-segment signal could let a long unbroken utterance hit the timeout. This is out of scope here and blocks CSAP rollout until the CSAP FR decides.
- **R2. Region policy.** `constraints/gcp.resourceLocations` blocked `eu` on 2026-10-29. If that returns, every call fails with a terminal `on_error`. Owned by VBOT-211.
- **R3. Latency to `eu` from the CSAP pods.** Not measured here; the CSAP FR measures it.

## Out of scope

- The cross-provider CLI (VR-014).
- CSAP wiring, quality comparison and field acceptance.
- Terraform, IAM and region policy (VBOT-211).
- Failover (VBOT-213).
- Language identification.
- New callbacks.
- Non-streaming TTS or container parsing.
- Replay or deduplication.
- Any other CI or hook change.
- Tags and publication.

## Source references

- `voice_runtime/providers/__init__.py` (protocols; `speak` has `stop_event: threading.Event | None`)
- `voice_runtime/providers/azure_stt.py` (`_MAX_RECONNECT_ATTEMPTS = 3`, `_schedule_owned`/`_owned_futures`, `ECHO_DISCARD_WINDOW_S`, `_build_recognizer`)
- `voice_runtime/providers/azure_tts.py` and `elevenlabs_tts.py` (result keys: empty text, pre-disconnect, interrupted, error, mark timeout)
- `voice_runtime/session.py` (`is_disconnected` property; `send_mark_and_wait` returns without error on disconnect and is a no-op when there is no loop)
- `voice_runtime/stt.py`, `voice_runtime/tts.py` (factories and their `ValueError`)
- `scripts/probe_vr009_lid.py`; `reference/release-checklist.md`
