# VR-013 — Google Chirp 3 STT and TTS providers

**Priority:** MEDIUM
**Type:** Feature. It adds providers behind the existing `SttProvider`/`TtsProvider` protocols. Existing providers, transports, the session and the protocols are unchanged.
**Status:** Implemented offline (AC-01 to AC-11, AC-13 met). AC-12 live run 1 (2026-11-16) returned FAIL; owner reviewed and recommended natural-paragraph input; Amendment A-2 drafted, awaits owner answers and round-3 judgement. Earlier: approved with revisions (round 1), R-1 to R-6 folded, Q1–Q3 answered, Amendment A-1 judged in round 2 with owner waiver.
**Requested:** 2026-11-16
**Jira:** VBOT-212, under epic VBOT-210.
**Parent:** [VR-012](VR-012-google-chirp-stt-tts-providers.md), split by its [round-1 judgement](VR-012-google-chirp-stt-tts-providers.judgement.md). This FR owns VR-012's D-2 and D-5 surfaces and its revisions R-2, R-3, R-4, R-6 and R-7. In the rest of this FR, R-1 to R-6 mean the revisions of this FR's own judgement. The cross-provider CLI is [VR-014](VR-014-speech-check-cli.md); this FR does not depend on it.
**Effort:** 2.5–3 days: two providers, fake-client tests, the Python 3.11/3.12 matrix, two probe scripts and the live probe.
**Target release:** the next **minor** release after the actual release baseline at enforcement (`reference/release-checklist.md`). From today's unreleased 0.4.0 that is 0.5.0. If VR-014 releases first, it is the minor after that.
**First consumer:** CSAP, through a separate CSAP FR. CSAP rollout stays blocked on that FR's timer decision and field acceptance (R1 below), not on this library.
**Research:** [evidence/VR-012-research.md](evidence/VR-012-research.md): region, SDK surface, streaming behaviour, Google limits, and the probe sources as run (§7).
**Prior art:** NC-161, NC-258 and NC-340 (Azure persistent STT, reconnect, feed survives reconnect); VR-005 (owned tasks); VR-009 (commit log line without text); NC-260 (`on_error` contract); NC-199/NC-203 (`on_recognizing`); `scripts/probe_vr009_lid.py` (script layout only; it calls the Azure SDK directly).
**Judgement:** [round 1](VR-013-google-chirp-speech-providers.judgement.md): APPROVED WITH REVISIONS; promoted by the owner on 2026-11-16. Revisions R-1 to R-6 below refer to that judgement.

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
| `project` | first non-empty of `GOOGLE_SPEECH_PROJECT`, `GOOGLE_CLOUD_PROJECT` | none: missing → `ValueError` at `start()` |
| `location` | `GOOGLE_SPEECH_LOCATION` | `eu` |
| `model` | `GOOGLE_STT_MODEL` | `chirp_3` |
| `language_code` | `GOOGLE_STT_LANGUAGE` | `fi-FI` |
| `phrase_hints` | `GOOGLE_STT_PHRASES` (comma-separated) | none |
| `endpointing` | `GOOGLE_STT_ENDPOINTING` | `supershort`; other values `short`, `standard`; anything else → `ValueError` in the constructor |

**Configuration rules (R-4).**

- **Precedence.** An explicit non-`None` argument wins over the environment; the environment wins over the default. The same rule applies to `GoogleTTS`.
- **Project.** An empty environment value is skipped, so the first non-empty one is used. An explicitly passed empty `project=""` raises `ValueError` in the constructor. When no project is found, STT `start()` raises `ValueError` before any SDK request.
- **`phrase_hints: Sequence[str] | None`.**
  - `None` reads `GOOGLE_STT_PHRASES`, split on commas.
  - An explicit sequence overrides the environment; an explicit empty sequence disables adaptation.
  - Entries are stripped, empty entries dropped and order kept, for both sources.
  - A non-string entry raises `ValueError`. A `str` passed as the argument raises `ValueError`; it is never split into characters.
- **Endpointing mapping** to the real SDK enum `StreamingRecognitionFeatures.EndpointingSensitivity`: `supershort` → `ENDPOINTING_SENSITIVITY_SUPERSHORT`, `short` → `ENDPOINTING_SENSITIVITY_SHORT`, `standard` → `ENDPOINTING_SENSITIVITY_STANDARD`.
- The supported models, regions and voices are not widened beyond this table.

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
- The END timestamp is used up and cleared by **every** final result, including empty or discarded ones, and is cleared when a stream is replaced (VR-012 R-3).

**Session owner (VR-012 R-3).** One owned task, `_run()`, created through the VR-005 owned-task pattern, is the only code that opens, rotates, reconnects or closes streams. Rotation and reconnect therefore cannot overlap.

Each stream works like this:

1. The request generator yields the config request, then 800-byte (100 ms) audio requests taken from a provider-owned carry buffer. The carry buffer is filled from `inbound_queue`.
2. **Pacing (R-1): at most 1× real time, always**, including after a reconnect or rotation. Decision D-1 explains why.
   - The generator schedules on `time.monotonic()`. Each audio request may be sent no earlier than the previous one's send time plus the audio duration of the previous request: 100 ms for a full 800-byte request.
   - A late scheduler does not catch up: after a delay, the next request waits its full interval from the actual previous send, so there is never a burst.
   - The final short batch at end of input is a request like any other; the request after it, if any, waits for its audio duration (for example 60 ms for 480 bytes).
   - Buffer size does not show whether there is a backlog: a full 800-byte batch is also the normal send unit. The rule is the same for normal arrival and backlog.
   - **Consequence:** audio queued during a reconnect or rotation (up to ~7 s of backoff plus the 5 s drain) stays as a lasting delay for the rest of the session. The README states this. A faster rate needs separately judged service evidence.
3. Bytes leave the carry buffer only when the generator yields them. Bytes not yet yielded, including a part-filled batch of 1–4 frames, survive rotation and reconnect, in order.
4. **Normal end of input (R-2).** When the input sentinel `None` is taken from `inbound_queue`, the generator yields any final short batch and ends the request stream. The provider then processes remaining responses for at most `_DRAIN_TIMEOUT_S` = 5 s, cancels the call if it is still open, and exits. It does not reconnect and does not call `on_error`. The response stream ending after this point is normal EOF, not unexpected EOF.

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
| Retryable | `ServiceUnavailable`, `DeadlineExceeded`, `InternalServerError`, `Aborted`, or unexpected EOF (below) | Reconnect after backoff 1 s, 2 s, 4 s (doubling, at most 30 s). |
| Terminal | `PermissionDenied`, `Unauthenticated`, `InvalidArgument`, `NotFound`, `ResourceExhausted` | `on_error("google_stt_<snake_name>")` once, then the session ends. |

- Retryable errors are handled the same way whether they happen when the stream is opened, while it is read, during a rotation drain or when the replacement stream is opened.
- **Unexpected EOF** is the server ending the response stream while the provider is still sending: not after the `None` sentinel (normal EOF, §1 step 4) and not after a rotation ended the request stream. It has no exception class, so its exhausted reason is the fixed string `reconnect_exhausted_UnexpectedEOF`.
- **Attempt counting (R-2).** One initial failure plus **3** failed replacement attempts, 4 failed streams in total:
  - stream 1 fails → wait 1 s → replacement 1 fails → wait 2 s → replacement 2 fails → wait 4 s → replacement 3 fails → `on_error("reconnect_exhausted_<ExceptionName>")` once, and the session ends. There is no fourth replacement.
  - A replacement **succeeds** when it delivers any response or runs 5 s without error, whichever comes first. A success resets the allowance: a later failure starts a new count of 3 replacements and a new backoff from 1 s.
- **Terminal errors** at any point, including during a replacement open or a drain, end the session at once with the terminal callback; no reconnect follows.
- **Collisions.** When a rotation is due during a backoff, or a retryable error hits during a rotation drain, the retry policy wins: the next stream is one replacement attempt, not a rotation plus a reconnect. At most one stream is open at any time, and `stop()` overrides both.
- The terminal callback (`google_stt_*` or `reconnect_exhausted_*`) fires at most once per provider instance.

**`stop()`.**

- Sets a stopped flag that every transition checks. Stop is honoured during backoff, drain and opening, and nothing restarts after it.
- Cancels and awaits the owned tasks (VR-005).
- Closes the client transport (`await client.transport.close()`).

**No-loss claim (VR-012 R-3).** The provider guarantees only that **locally kept audio that was never handed to the SDK** is sent once to the next stream, in order. Audio already handed to a stream that later failed was not acknowledged and may be lost. A sentence cut by a forced rotation may lose words or never be committed. AC-12 measures this live, and owner question Q2 decides whether the release accepts it.

### 2. `voice_runtime/providers/google_tts.py`: `GoogleTTS`

**Constructor.** `project`, `location` and `voice_name` are optional and follow the §1 precedence and project rules. `voice_name` falls back to `GOOGLE_TTS_VOICE`, default `fi-FI-Chirp3-HD-Charon`. The language code is the first two parts of the voice name (`fi-FI`). The client is `TextToSpeechClient(client_options=ClientOptions(api_endpoint=f"{location}-texttospeech.googleapis.com", quota_project_id=project))`, so `eu-texttospeech.googleapis.com` by default. A missing project raises `ValueError` from `speak()` before any SDK request, but only after the three no-request early returns below.

**`speak(text, session, stop_event=None)`.** Result keys are the same as in `AzureTTS` and `ElevenLabsTTS`:

| Case | Behaviour | Result |
|---|---|---|
| `text` empty or whitespace only | no request | `{"last_spoken": ""}` |
| `session.is_disconnected` before start | no request | `{"last_spoken": "", "call_disconnected": True}` |
| `stop_event` already set | no request | `{"last_spoken": text, "interrupted": True}` |
| normal | `streaming_synthesize`: a config request (voice, `StreamingAudioConfig(MULAW, 8000)`), then one text request. Each chunk is cut into 160-byte frames → `put_outbound_sync` + `tap_agent`. A partial tail carries over to the next chunk. At normal completion only, the final partial frame is padded with `0xFF`. Then `send_mark_and_wait("tts_complete", timeout=30.0)`. | `{"last_spoken": text}` |
| stop_event set mid-stream | Cancel the call. Returns within **1 s** of the event (one budget, below), even if the SDK read is blocked. No more frames, no padding, no mark, no `on_error`. | `{"last_spoken": text, "interrupted": True}` |
| session disconnected mid-stream | Cancel the call; no mark, no `on_error`. Same 1 s budget. | `{"last_spoken": text, "call_disconnected": True}` |
| Google API error (at call or in the worker) | `on_error("google_tts_<snake_name>")` | `{"last_spoken": text, "error": str(exc)}` |
| mark `TimeoutError` | Warning log, as in the existing providers. | `{"last_spoken": text}` |
| disconnected after mark wait | `send_mark_and_wait` returns without error when the call drops, so `is_disconnected` is checked afterwards. | `{"last_spoken": text, "call_disconnected": True}` |

**Worker and cancellation (R-5).**

- **One worker per call.** A non-daemon worker thread reads the response stream and only puts messages on a `queue.Queue`: `("audio", bytes)`, `("eof", None)` or `("error", exc)`. It never touches the session. Only `speak` emits frames, taps and marks.
- **Polling.** `speak` waits on the queue for at most 50 ms at a time and checks `stop_event` and `is_disconnected` between waits. A worker `error` message ends `speak` through the API-error row, so `speak` can never poll forever.
- **One cancellation budget.** When the stop event or disconnect is seen, `speak` takes `deadline = time.monotonic() + 1.0` (measured from the poll that saw it, at most 50 ms after the event; the AC measures from the event itself). Within that budget it calls the streaming call's `cancel()` and joins the worker with the time remaining. The budget is shared, not added per step.
- **No escape.** Every exit after the worker starts (normal completion, interruption, disconnect, API error) cancels or finishes the call and joins the worker. If the worker is still alive when the budget ends, that is a defect a test must catch; there is no daemon thread, detached work or silent join timeout. A caller-requested cancellation surfaces as `interrupted` or `call_disconnected`, never as a Google API error.
- **Raw bytes kept as-is.** Nothing is sniffed or stripped. Chunk bytes are passed through unchanged; there is no fallback to non-streaming synthesis.
- Mark-wait behaviour and the public protocol are unchanged; session-level mark cancellation is out of scope.

### 3. Factories, packaging, configuration

- `stt.py`: `get_stt_class("google")` and `create_stt("google", **kw)`. `tts.py`: `create_tts("google", **kw)`. All three use lazy imports, and docstrings list `google`. Unknown names keep today's `ValueError`.
- `pyproject.toml` gains `google = ["google-cloud-speech>=2.41.0", "google-cloud-texttospeech>=2.38.0"]`. The floors are lowered only through AC-10.
- **D-6 (owner question Q3 must be answered first; conditional):** `.github/workflows/publish.yml`'s test-job install line becomes `".[dev,elevenlabs,azure,google]"`. No other workflow change.
- Authentication is ADC only. No credential is read, stored or logged by the provider.

### 4. Probe scripts (D-4), outside the package

- `scripts/probe_vr013_sdk_surface.py`: the research §7.1 probe, extended to every SDK name the providers use. It prints versions and exits non-zero if a name is missing.
- `scripts/probe_vr013_google_session.py` (R-3, simplified by Amendment A-1) drives the **real** `GooglePersistentStt` (async client) and `GoogleTTS`. Synthetic Finnish text only, ADC with an explicit `--project`. It proves one thing live: an ordinary swap keeps a long session transcribing without loss. The forced swap is proved offline only (AC-06).
  - **Capture session.** A probe-local subclass of `VoiceSession` overrides `put_outbound_sync` to append bytes to a buffer exactly once, and has no event loop, so `send_mark_and_wait` is a no-op. (`probe_vr009_lid.py` calls the Azure SDK directly and is not a model for this.) The probe asserts each capture is non-empty and a whole number of 160-byte frames before feeding STT.
  - **Phrases.** Each phrase is `"Kohta <word>."` followed by 1.5 s of silence, where `<word>` comes from a fixed list of distinct Finnish words (no numbers, so no number normalisation), never reused within a run.
  - **Run.** One mode only. It feeds 300 s of audio at 20 ms per frame, has an overall timeout of 360 s, and bounds cleanup (`stop()`, then exit) to 15 s.
  - **Pass.** A rotate line with `forced=false` appears after 270 s, and every phrase word is committed once, in order, with nothing missing or duplicated. A missing rotation, a provider error, a timeout or an empty capture is a failure.
  - **Output.** Versions, git SHA and the exact command; the stream-open and rotation times; the expected and committed word sequences with any missing or duplicated words; errors; and the TTS time to first chunk, measured at the first non-empty response from the SDK.
  - Example: `python scripts/probe_vr013_google_session.py --project scp-tenant-csap-test > tmp/vr013-live.log 2>&1`.

### Not changed

- Existing providers and transports.
- `VoiceSession`.
- The `SttProvider`/`TtsProvider` protocols.
- Default provider names and existing environment variables.
- Installs without the `google` extra.

There is no new callback and no synthetic periodic `recognizing`.

## Amendment A-1 — 2026-11-16 (owner; awaits round-2 judgement)

**Change.** The owner's Q2 answer removes the forced-rotation live run and the loss analysis from R-3:

- §4: one live probe mode (ordinary swap), unique word IDs, a plain in-order check of the committed words. Removed: `--mode forced`, phrase-content normalisation, loss intervals.
- AC-12: passes on an observed ordinary rotation with no missing or duplicated phrase word. Forced-swap loss is not measured live.
- Q2 no longer blocks release.

**Unchanged.** The rotation design itself (§1: ordinary swap after 270 s, forced swap at 290 s, drain at most 5 s, then open the new stream) and its offline witnesses in AC-06 are unchanged.

**Why.** R-3 requires an amended, judged probe plan when the forced path is not exercised live. The first consumer ends calls at 300 s, so loss after 290 s has no effect on it (Q2 answer).

## Frozen scope (judgement round 1)

| Deliverable | Surface |
|---|---|
| D-1 | This FR, its implementation and evidence record, and directly cited sanitised research updates. |
| D-2 | `voice_runtime/providers/google_stt.py` and `google_tts.py`; the Google branches and docstrings in `voice_runtime/stt.py` and `tts.py`. |
| D-3 | `tests/test_vr013_google_stt.py`, `tests/test_vr013_google_tts.py`, the AC-02 subprocess witness, and the directly needed factory and NC-165 conformance cases. |
| D-4 | `scripts/probe_vr013_sdk_surface.py` and `scripts/probe_vr013_google_session.py`. No shipped diagnostic CLI. |
| D-5 | The `pyproject.toml` Google extra and next-minor version; the related `README.md`, `.env.example` and `CHANGELOG.md` updates. |
| D-6 | Only the Google-extra addition to the test-job install line in `.github/workflows/publish.yml`, after Q3. |

Anything outside this table, including VR-014, CSAP or black-box edits, CI beyond D-6, and tags or publication, needs its own authority.

## Acceptance criteria

Revised in judgement round 1. Offline criteria (AC-01 to AC-11) make no network request and use no ADC.

- [x] **AC-01 (behavioural RED).** A test-only commit adds `tests/test_vr013_google_stt.py` and `tests/test_vr013_google_tts.py`, run with the exact SDK floors installed, before any GREEN commit.
  - Tests reach the providers only through the existing factories (`get_stt_class`, `create_stt`, `create_tts`) and never import `voice_runtime.providers.google_*` at module level.
  - In RED, the only allowed failures are the factories' existing `ValueError("Unknown … provider: google")` and assertion failures. A collection, fixture or dependency error makes the RED invalid.
  - Every new or changed VR-013 test carries `@pytest.mark.req("VR-013")`.
  - The SDK client classes are faked; requests, responses and enums are the real SDK message types.
- [x] **AC-02 (optional SDK, R-6).**
  - SDK-dependent test modules start with `pytest.importorskip("google.cloud.speech_v2", reason="optional google extra not installed")` (and the matching TTS module). This is the only allowed skip.
  - A lazy-import witness runs a **fresh subprocess** with a controlled environment and no network: it imports `voice_runtime`, `voice_runtime.stt` and `voice_runtime.tts`, creates Azure STT and Azure TTS through the factories, and asserts that no `google.cloud` module is in `sys.modules`. It runs, and passes, both with and without the Google extra installed.
- [x] **AC-03 (STT config, R-4).**
  - The first request of the initial stream **and of every replacement stream** contains: the recognizer path, `MULAW`/8000/1 decoding, the model, `language_codes=["fi-FI"]`, `interim_results=True`, `enable_voice_activity_events=True` and the mapped endpointing enum.
  - Adaptation appears only with hints; `["Terveystalo"]` produces exactly that phrase.
  - Each of the three endpointing values maps to its enum; any other value → `ValueError` in the constructor.
  - Endpoint `eu-speech.googleapis.com`, quota project equal to the project.
  - Precedence: an explicit argument beats the environment; `GOOGLE_SPEECH_PROJECT` beats `GOOGLE_CLOUD_PROJECT`; an empty `GOOGLE_SPEECH_PROJECT` falls through to `GOOGLE_CLOUD_PROJECT`; explicit `project=""` → `ValueError`; no project → `ValueError` at `start()` with no SDK request.
  - Hints: `GOOGLE_STT_PHRASES=" a, ,b "` → `["a", "b"]`; explicit `[]` disables adaptation even when the variable is set; a non-string entry or a bare `str` → `ValueError`.
- [x] **AC-04 (STT audio and pacing, R-1).** With a fake monotonic clock, assert payloads **and** send timestamps:
  - 25 normally arriving frames → five ordered 800-byte requests at least 100 ms apart.
  - While speaking, the bytes are `0xFF` of the same length.
  - Three frames then the sentinel → one 480-byte request.
  - A preloaded 40-frame backlog → eight 800-byte requests at least 100 ms apart, never faster.
  - Delayed scheduling: the scheduler is held back 500 ms; the next requests are still at least 100 ms apart from each actual previous send (no burst).
  - After a reconnect and after a rotation, the retained audio is sent at the same 1× pace.
- [x] **AC-05 (STT callbacks and lag).**
  - Final `" Hei."` → `on_committed("Hei.")` exactly once; a whitespace-only final → no call.
  - An interim result → `on_recognizing(text)`; BEGIN → `on_recognizing("")`.
  - Speaking and echo-window discards work as in Azure.
  - Log line format: exact lag after END, `-1` without END. An END followed by an empty final, then a later final with no new END, logs `-1`. An END before a rotation, followed by a final on the new stream, logs `-1`.
  - The transcript text never appears in a log record.
- [x] **AC-06 (rotation and lifecycle).** With a fake clock and fake streams, assert the actual stream sequence and the bytes kept:
  - ordinary rotation at the first final after 270 s (`forced=false`);
  - forced rotation at 290 s with a **silent server** (no responses, no frames) (`forced=true`);
  - a blocked old-stream drain is cancelled after 5 s;
  - a 1–4-frame partial batch and the queued frames reach the next stream exactly once, in order;
  - a retryable error during the drain or the replacement open is handled as one replacement attempt; a terminal error there ends the session with one callback;
  - a rotation falling due during a backoff does not open a second stream; the fake records the number of open streams, always ≤1;
  - normal end of input: the short batch is sent, responses are drained for at most 5 s, a stalled call is cancelled, and the provider exits with no reconnect and no `on_error`;
  - `stop()` during the drain, the opening and the backoff each leave no owned task pending and open no further stream; the client transport is closed;
  - `on_error` is never called by a rotation.
- [x] **AC-07 (retry policy, R-2).**
  - Each retryable class, and unexpected EOF, at open and while reading → reconnect with the same config request.
  - Exhaustion sequence: initial failure, replacements 1, 2 and 3 fail → exactly 4 opens, backoff 1, 2 and 4 s, then exactly one `on_error("reconnect_exhausted_<Name>")`; no fifth open. For unexpected EOF the reason is `reconnect_exhausted_UnexpectedEOF`.
  - Reset by response: fail, replacement delivers a response, fail ×4 → `on_error` only after the last, and the second series starts its backoff at 1 s.
  - Reset by time: the same sequence, with the replacement running 5 s without error and without responses.
  - Each terminal class, at open and while reading → one `on_error("google_stt_<snake_name>")`, no reconnect.
  - Normal EOF after the sentinel and a successful rotation produce no error and no reconnect.
- [x] **AC-08 (TTS, R-4, R-5).**
  - With a fake client yielding raw chunks of 333, 160 and 7 bytes, including `RIFF` and `0xFF` byte values inside the payload: the frames concatenate to the input with only end padding; the config request is as in §2; the mark is sent once.
  - Configuration: endpoint `eu-texttospeech.googleapis.com`, quota project, default voice `fi-FI-Chirp3-HD-Charon` with language `fi-FI`, explicit-over-environment precedence; a missing project → `ValueError` with no SDK request, after the three early returns still return their results without a request.
  - Every other row of the §2 table has its own test, including `stop_event=None`, mark timeout and disconnect after the mark wait.
  - Cancellation, with a fake whose next read **blocks until cancelled**: (a) stop set and (b) disconnect → `speak` returns within 1 s of the event; (c) the worker raises a Google API error → error result and one `on_error`. In each case, and after a normal completion, the worker thread has ended (`is_alive()` false) and no frame, tap or mark appears after return. (a) and (b) call no `on_error`.
- [x] **AC-09 (factories).**
  - `get_stt_class("google")`, `create_stt("google", project="p")` and `create_tts("google", project="p")` return the new types.
  - The NC-165 protocol-conformance tests include the Google cases under the AC-02 skip policy; existing provider cases stay active with unchanged assertions.
- [x] **AC-10 (Python and SDK matrix, R-6).**
  - Fresh venvs on **Python 3.11 and 3.12** with `.[dev,azure,elevenlabs]` plus exactly `google-cloud-speech==2.41.0` and `google-cloud-texttospeech==2.38.0`: `scripts/probe_vr013_sdk_surface.py` passes and the **full offline suite** passes with no optional-Google skip.
  - A second fresh venv per version with `.[dev,azure,elevenlabs]` only (Google never installed): the full suite passes; only the new SDK-dependent cases skip, and the AC-02 subprocess witness and all existing-provider tests run.
  - The record holds the install and test commands, interpreter versions, resolved SDK versions (`pip freeze` excerpt), exit codes and summaries.
  - A lower floor is allowed only if this same evidence passes at that exact version.
- [x] **AC-11 (artifacts).**
  - `ruff check voice_runtime/` (release checklist) passes.
  - `python -m build` and `twine check` pass.
  - A clean non-editable wheel install with `[google]`, run outside the checkout, creates both providers through the public factories without a live request.
- [ ] **AC-12 (live, Q1 approved; R-3 as amended by A-1).** Run `scripts/probe_vr013_google_session.py` once against `scp-tenant-csap-test` and record the probe output listed in §4.
  - The following block release and are recorded as failure evidence: a 403, 404 or region error; any provider error or timeout; no ordinary rotation observed; any missing or duplicated phrase word.
  - Forced-rotation loss is not measured live (Q2).
- [x] **AC-13 (docs).**
  - `README.md` gains a Google STT and TTS section covering:
    - the extra, ADC and quota project (workload identity in pods);
    - the configuration table and precedence rules;
    - the restriction to Chirp 3 HD voices;
    - **interface conformance only**: `on_recognizing` cadence differs from Azure (fact 4);
    - the meaning of `commit_lag_ms`;
    - 1× pacing, so a reconnect or rotation leaves a lasting delay;
    - the rotation limits, and that a forced swap at 290 s may lose the speech in flight; consumers whose sessions can run past 290 s with continuous speech must accept this.
  - `.env.example` gains commented Google variables.
  - `CHANGELOG.md` records VR-013 under the actual next minor, and the version is bumped. D-6 changes only after Q3. Tagging and publishing need the owner's explicit "publish".
- [ ] **AC-14 (record).** This FR records the folded revisions, RED and GREEN commits, decisions and deviations, the AC-10 and AC-11 evidence, the AC-12 output, and the owner answers to Q1–Q3. An unanswered or declined gate is recorded as blocked, not fulfilled.

## Implementation record (2026-11-16)

### Commits (branch `feat/vr-013-google-chirp-providers`, local, unpushed)

| Commit | Content |
|---|---|
| `302977d` | Round-2 owner decision record (judgement file, Q2 waiver). |
| `2d7e090` | RED: `tests/test_vr013_google_stt.py` (85), `tests/test_vr013_google_tts.py` (27), `tests/test_vr013_lazy_import.py`, Google cases in `tests/test_factories.py` and `tests/test_nc165_stt_protocol.py`, `req` marker registration in `tests/conftest.py`. Committed with `SKIP=pytest`. |
| `3f14df3` | GREEN: `google_stt.py`, `google_tts.py`, factory branches, `pyproject.toml` 0.5.0 + `google` extra, README, CHANGELOG, `.env.example`, `publish.yml` (Q3), `scripts/probe_vr013_sdk_surface.py`. |
| `f17b5f0` | Refactor to pass the size and CC gates; behaviour unchanged. |
| `3152539` | `scripts/probe_vr013_google_session.py` (AC-12 probe). |

### Offline evidence

- **AC-01 RED validity.** `2d7e090` checked out in a temporary worktree, `pytest tests --no-cov`: 118 failed, 679 passed, 1 skipped, 0 errors. Failures: 81 `Unknown STT provider: google`, 27 `Unknown TTS provider: google`, 10 assertion mismatches (`pytest.raises(match=…)` on the factory error). No collection, fixture or dependency error.
- **GREEN.** `pytest tests --no-cov` at `f17b5f0`: 797 passed, 1 skipped (ElevenLabs key-gated integration test). The five VR-013-related files ran 5 times in a row: 143 passed each time.
- **AC-10** (`tmp/matrix.sh` at `3152539`, fresh `uv` venvs, `google-cloud-speech==2.41.0`, `google-cloud-texttospeech==2.38.0`, all install/probe/pytest rc=0):

  | Interpreter | Google extra | SDK surface probe | `pytest tests` |
  |---|---|---|---|
  | 3.11.14 | yes | OK 46 names | 797 passed, 1 skipped |
  | 3.11.14 | no | — | 679 passed, 9 skipped (8 `optional google extra not installed`, 1 ElevenLabs key) |
  | 3.12.13 | yes | OK 46 names | 797 passed, 1 skipped |
  | 3.12.13 | no | — | 679 passed, 9 skipped (as above) |

- **AC-11** (`tmp/ac11.sh` at `3152539`): `ruff check voice_runtime/` passes; `python -m build` builds `voice_runtime-0.5.0` sdist and wheel; `twine check` PASSED for both; a clean non-editable install of the wheel with `[google]`, run from `/tmp` with an empty environment and a dead proxy, creates `GooglePersistentStt` and `GoogleTTS` via the factories (rc=0). The build prints a setuptools deprecation warning about the `project.license` TOML table; it is not caused by this change and is not addressed.
- **Gates.** `pre-commit` (ruff, file-size ≤450, radon CC <21, pytest) passes on `f17b5f0` and `3152539`: `google_stt.py` 450 lines, `GoogleTTS.speak` CC 17, highest STT method CC 11.

### AC-12 live run 1: FAIL, for owner manual review

Command (ADC, synthetic Finnish only): `.venv/bin/python scripts/probe_vr013_google_session.py --project scp-tenant-csap-test > tmp/vr013-live.log 2>&1`, exit 1.

Probe output (verbatim, abridged lists):

```
git: 3152539fc44dc4235a6e4a4d42ea93697b6a7749 python: 3.14.7
voice-runtime 0.1.12   (stale editable-install metadata; code imported from the checkout at 3152539)
google-cloud-speech 2.41.0 / google-cloud-texttospeech 2.38.0 / google-api-core 2.44.0
tts: phrases=98 synth_s=26.7 ttfc_ms first=407 median=165 max=407
feed_start_s=0.77
stream_open_s=1.03,274.42
rotate at_s=274.42 since_first_open_s=273.39: Google STT stream rotate age_s=273 forced=false drained=true
expected (98) / committed (101)
errors: []
RESULT FAIL: committed words differ from expected sequence
```

What passed: no 403/404/region error, no provider error, no timeout, no forced rotation. One ordinary rotation (`forced=false`, `drained=true`) at 273.4 s after the first stream open.

What failed: the strict word-sequence rule (AC-12, "any missing or duplicated phrase word"). Aligning expected against committed words (difflib, after removing a leading `kohta`/`ohta` from committed tokens) shows three distinct causes:

1. **Probe scoring artefact (about 25 words).** Chirp often joins the carrier word to the target word (`kohtametsä`, `kohtajärvi`, …). The probe only discards a standalone `kohta` token, so each joined word counts as both missing and unexpected. With the prefix removed, these words all match, in order.
2. **Recognition errors (substitutions, not loss).** `sade`→`fari`; `orava siili`→`seuraava fiili`; `varis`→`lais`; `delfiini`→`corpo dela filme`; `kilpikonna`→`kilpikonnat`; one inserted `kohpa`, one inserted `pultti`. The audio for each phrase was transcribed; the words are wrong.
3. **Two findings about stream timing:**
   - **End of run.** The last two phrases (`pallo`, `nukke`, fed in the final seconds before 300 s) were never committed. After the rotation the provider runs behind the feed at 1× pacing (D-1, up to the 5 s drain plus the open). The probe waits only `TAIL_WAIT_S = 6.0` after the end-of-input sentinel before `stop()`, which is likely too short for the backlog plus the last final. This looks like a probe defect; it is not yet confirmed by a re-run.
   - **Phrase split at the rotation.** `Kohta norsu`, the phrase in flight when the ordinary rotation fired, was committed as `kohtan` + `orsu`. Part of the utterance went to the old stream and the rest to the new one. No word in the phrase list was lost at the boundary, but the word was split across the two streams. This is consistent with D-2 (drain before open, no overlap). The README does not currently mention that an ordinary rotation can split a word.

Owner decision needed (not taken by the implementer):

- whether AC-12's exact-sequence rule stands, or is amended (e.g. tolerate the carrier prefix and report substitutions while failing only on errors, no rotation, or a run of missing words near the rotation); an AC change needs an amendment and re-judge;
- whether to fix the probe tail wait and the `kohta` prefix handling (D-4 scope) and re-run;
- whether the split-at-rotation finding goes into the README and Open risks.

Raw output: `tmp/vr013-live.log` (not committed).

**Owner manual review (2026-11-16, Sami Heikkinen):** "chirp stt seems to add extra kohta- prefix on multiple cases. otherwise the wordlist seems okayish. recommendation: test with real paragraphs, not just a wordlist to see the effect more natural setting."

- Implementer note: `Kohta` is spoken in every probe phrase (`"Kohta <word>."`). Chirp does not invent it; it often writes it joined to the next word (`kohtametsä`). A carrier word before an isolated noun is an unnatural input, which supports the owner's recommendation.
- Owner follow-up (2026-11-16): "if kohta was in the real audio, then this is just punctuation issue, i.e. minor". The joined carrier word is classified as a **minor word-boundary/punctuation issue**, not as missing or invented content.
- Run 1 therefore stays recorded as FAIL against the current rule. The recommended next step is Amendment A-2 below, which needs judging before the probe changes.

### Amendment A-2 — 2026-11-16 (draft; awaits owner confirmation and round-3 judgement)

**Change (proposed).** Replace the AC-12 word-list probe with natural-paragraph input:

- **Input.** About 12 to 15 fixed, distinct synthetic Finnish paragraphs, each 2 to 4 sentences. Use everyday or clinic-reception style text with no real caller or patient data and no numbers. Each paragraph is followed by 1.5 s of silence, and the layout fills 300 s, as now.
- **Scoring.**
  - Normalise both sides: lowercase and strip punctuation.
  - Align the reference words with the committed words and report the substitutions, insertions and deletions, the word error rate (WER), and the alignment within ±5 s of the rotation.
- **Pass.**
  - No provider error or timeout.
  - One ordinary rotation (`forced=false`) after 270 s and no forced rotation.
  - No run of 3 or more consecutive deleted reference words anywhere, which would indicate lost audio rather than misrecognition.
  - WER is reported, not gated (open question below).
- **Probe defect fix.** After the end-of-input sentinel, wait until no commit has arrived for 3 s, at most 15 s, instead of a fixed 6 s. This leaves the post-rotation backlog time to drain (D-1) before `stop()`.
- **Docs.** README and Open risks add that an ordinary rotation can split the utterance in flight across two streams (run 1: `norsu` → `kohtan` + `orsu`).

**Open questions for the owner.**

1. Should WER have a release threshold, or is it reported only?
2. Is a 3-word deletion run the right loss signal?

**Unchanged.** The provider code, the rotation design (D-2), and AC-01 to AC-11 and AC-13.

### Decisions and deviations made during enforcement

- **Deviation from D-2 file list.** I added a new private module, `voice_runtime/providers/_google_config.py`, which is not in the D-2 file list. It holds the shared project/env resolution, defaults, hint parsing, endpointing validation and the first-request builder. It was needed to bring `google_stt.py` under the 450-line gate (553 lines before) and it removes logic duplicated between the providers. `_build_config_request()` is still the single builder and delegates to `SttSettings.config_request()`. The module imports the Speech SDK only inside that method.
- **Test registration.** The `req` marker is registered in `tests/conftest.py`, because it was not registered before.
- **`publish.yml`.** The approved diff omitted `-e`; the actual line is `pip install -e ".[dev,elevenlabs,azure,google]"`. The editable flag was already there and is unchanged.
- **TTS behaviour:**
  - The client is created lazily on the first `speak`.
  - A cancel during a blocked first read closes the client transport and drops the cached client.
  - Any worker exception counts as an API error result.
  - Logs show the character count only.
  - The queue polling loop is extracted to `_pump` to meet the CC gate.
- **STT behaviour:**
  - An unclassified exception is treated as terminal.
  - After the input ends, a retryable error or EOF is only logged; a terminal error still fires its one callback.
  - A retryable error during a rotation drain starts a new series at attempt 1.
  - The rotate log line is written only after a successful rotation.
  - Endpointing values must match exactly in lowercase.
  - Interim results and BEGIN are ignored while speaking.
  - The speaking mute is decided when a frame enters the carry buffer.
- **Process gap.** `pre-commit` hooks are not installed in this repository (`.git/hooks` has no `pre-commit`). `2d7e090` and `3f14df3` were committed without the gates running; running them afterwards found the size and CC violations that `f17b5f0` fixes. From `f17b5f0` on, `pre-commit run --files …` was run manually before each commit.
- **Not done, needs owner:** push, tag, publish (AC-13: "publish" needed).

## Decisions

- **D-1 Pacing is 1× real time (R-1).** The evidence supports 800-byte requests every 100 ms at about real-time pace (research §4, fact 6), not any faster rate. After a reconnect or rotation the queued audio is sent at 1×, so the outage delay (up to ~7 s of backoff plus the 5 s drain) stays for the rest of the session, and the README says so. A catch-up rate above 1× needs separately judged service evidence.
- **D-2 Drain before open.** Following the parent judgement, the old stream is closed before the new one opens. There are no overlapping streams and no replay or deduplication.
- **D-3 Unexpected-EOF reason.** EOF has no exception class, so its exhausted reason is the fixed `reconnect_exhausted_UnexpectedEOF` (R-2).

## Owner questions (VR-012 R-7). Answer and record before the affected action

1. **Q1.** May `scp-tenant-csap-test` be used for the AC-12 billable synthetic-only probes? *(blocks AC-12)*
   - **Answer (2026-11-16, Sami Heikkinen): yes.** AC-12 may run against `scp-tenant-csap-test`, synthetic Finnish text only.
2. **Q2.** What measured maximum missing content or time interval at a forced rotation boundary is acceptable for this library release, and do the recorded AC-12 runs meet that bound? CSAP deployment stays separately gated; this does not authorise replay or a CSAP policy change. *(blocks release)*
   - **Answer (2026-11-16, Sami Heikkinen): implement a simple swap; no loss bound and no loss analysis.** The first consumer ends every call at 300 s: the CSAP supervisor sends `call_time_exceeded` at `VOICE_MAX_CALL_DURATION`, default 300 (CSAP `services/supervisor.py:112`), and the navigator then plays a fixed goodbye with no further listening (CSAP `config/voice_coordinator_navigator.yaml:139-143`, NC-364). Speech after 290 s is therefore lost anyway. Forced-swap loss is accepted, documented (AC-13) and not measured live. Applied as Amendment A-1.
   - **Round-2 waiver (2026-11-16, Sami Heikkinen):** the owner explicitly accepts unmeasured forced-swap loss of an in-flight utterance, including content before the cut, for this release. The round-2 judge revisions were not promoted; see the judgement file.
3. **Q3.** Do you approve the one-line `publish.yml` install change in §3, after reviewing its exact diff? *(blocks D-6)*
   - **Answer (2026-11-16, Sami Heikkinen): approved.** The diff is `pip install ".[dev,elevenlabs,azure]"` → `pip install ".[dev,elevenlabs,azure,google]"` in the test job, nothing else.

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
- Catch-up faster than 1× or any burst send.
- Overlapping streams.
- Real caller or patient audio; inspecting, storing or logging credentials.
- Any other CI or hook change.
- Tags and publication.

## Source references

- `voice_runtime/providers/__init__.py` (protocols; `speak` has `stop_event: threading.Event | None`)
- `voice_runtime/providers/azure_stt.py` (`_MAX_RECONNECT_ATTEMPTS = 3`, `_schedule_owned`/`_owned_futures`, `ECHO_DISCARD_WINDOW_S`, `_build_recognizer`)
- `voice_runtime/providers/azure_tts.py` and `elevenlabs_tts.py` (result keys: empty text, pre-disconnect, interrupted, error, mark timeout)
- `voice_runtime/session.py` (`is_disconnected` property; `send_mark_and_wait` returns without error on disconnect and is a no-op when there is no loop)
- `voice_runtime/stt.py`, `voice_runtime/tts.py` (factories and their `ValueError`)
- `scripts/probe_vr009_lid.py:54-64` (direct Azure SDK calls); `reference/release-checklist.md`
