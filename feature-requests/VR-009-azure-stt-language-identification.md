# VR-009 — Azure STT continuous language identification (fi-FI, sv-SE, en-GB)

**Priority:** MEDIUM
**Type:** Feature (opt-in provider configuration)
**Status:** Proposed
**Requested:** 2026-10-28
**Effort:** 0.5 day (library only; CSAP wiring and field test are CSAP NC-566)
**Target release:** next patch after 0.1.15 (number assigned at release; VR-008 may claim 0.1.16 first)
**First consumer:** CSAP. Its TEST worker sets `AZURE_STT_LANGUAGES=fi-FI,sv-SE,en-GB` (CSAP NC-566).
**Prior art:** NC-161 (Azure persistent STT), NC-258 (reconnect), VR-005 (task ownership); CSAP research `docs/2026-10-28-i18n-vs-finnish-bindings-research.md` (§3 provider matrix, Option 2, probe P2).
**Judgement:** pending

## Problem and ideal result

`AzurePersistentStt` recognises exactly one locale. The locale comes from the constructor argument `language_code` (default `"fi-FI"`, `voice_runtime/providers/azure_stt.py:53`) and is applied as `speech_config.speech_recognition_language` in `start()` (line 102) and again in `_reconnect()` (line 269). The media route constructs the provider with no arguments (`voice_runtime/transports/twilio_ws.py:245`, `session.stt_factory()`), so every consumer call is recognised as Finnish.

Field evidence (CSAP TEST, 2026-10-28, persona case HP-41, SUT call `CA6a6f98bb19c2fbd60d2ca77cac84277e`): a Swedish-speaking caller was transcribed by the `fi-FI` model into Finnish-shaped nonsense, a recap approval was recorded from a garbled reply, and the nurse ticket carried garbage background text. The harness reported PASS because it checks only smoke criteria.

**Ideal result:** a consumer can ask the Azure provider to recognise each caller phrase in one of a small, fixed set of locales (first target: `fi-FI`, `sv-SE`, `en-GB`), with no code change in the consumer, and can see per phrase which locale Azure detected. A consumer that configures nothing gets today's single-locale behaviour unchanged.

## Provider facts this FR relies on

Read from Microsoft Learn, "Implement language identification" (page updated 2026-08-10), fetched 2026-10-28:

1. Candidate languages go in `AutoDetectSourceLanguageConfig(languages=[...])`: at most 4 for at-start LID, at most 10 for continuous LID.
2. Full locales with a dash are required, and only one locale per base language (not `en-US` and `en-GB` together).
3. The service always returns one of the candidates, even when the spoken language is not among them.
4. Continuous LID needs `PropertyId.SpeechServiceConnection_LanguageIdMode = "Continuous"`, works only with continuous recognition, and does not detect a language change inside one sentence.
5. "Currently for speech to text recognition with continuous language identification, you must create a SpeechConfig from endpoint." The C++ sample names the endpoint: `wss://<region>.stt.speech.microsoft.com/speech/universal/v2`.
6. The detected locale is read per result with `speechsdk.AutoDetectSourceLanguageResult(result).language`. A `NoMatch` result may carry `Unknown`.
7. "For speech recognition, the initial latency is higher with language identification."

SDK surface checked in the CSAP venv (`azure-cognitiveservices-speech` 1.51.0; voice_runtime floors it at `>=1.38.0`): `speechsdk.languageconfig.AutoDetectSourceLanguageConfig`, `speechsdk.AutoDetectSourceLanguageResult`, `PropertyId.SpeechServiceConnection_LanguageIdMode` and `SpeechConfig(subscription=..., endpoint=...)` all exist.

**Not verified (field acceptance in NC-566):**
- whether continuous LID works for the `swedencentral` region used by CSAP TEST;
- detection accuracy on 8 kHz μ-law phone audio and short answers ("joo", "ja", "ok");
- the added latency.

## Proposed solution

1. **Configuration.** `AzurePersistentStt.__init__` gains `languages: Sequence[str] | None = None`. When `None`, the provider uses `AZURE_STT_LANGUAGES`, read and parsed once at module import, following the module's existing env-read pattern (`AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION`, lines 31-32). In CSAP the module is imported at worker start (`server_fsm.py:126` resolves the provider class), so a bad value stops the worker at start, not mid-call.
2. **One parser, `_parse_languages(raw) -> tuple[str, ...]`.**
   - Split on commas and strip whitespace; drop empty entries.
   - An empty result means "not configured".
   - Each entry must match `^[a-z]{2,3}-[A-Z]{2}$`.
   - Base languages (the part before the dash) must be distinct, per fact 2.
   - At most 10 entries, per fact 1.
   - Any violation raises `ValueError` naming the bad entry or rule.
   - The constructor argument goes through the same parser.
3. **Mode selection.**
   - Not configured → single-locale mode with `language_code`, the exact code path of today.
   - Exactly one entry → single-locale mode with that entry as the locale; no LID objects are created.
   - Two or more entries → continuous LID mode.
4. **Continuous LID mode.** One private `_build_recognizer()` builds the recognizer for both `start()` and `_reconnect()`, so a reconnect cannot silently fall back to single-locale mode. In LID mode it:
   - builds `SpeechConfig(subscription=key, endpoint=f"wss://{region}.stt.speech.microsoft.com/speech/universal/v2")`;
   - does not set `speech_recognition_language`;
   - sets `SpeechServiceConnection_LanguageIdMode` to `"Continuous"`;
   - keeps `SpeechServiceConnection_EndSilenceTimeoutMs` as today;
   - passes `auto_detect_source_language_config=AutoDetectSourceLanguageConfig(languages=list(locales))` to `SpeechRecognizer`.

   In single-locale mode `_build_recognizer()` produces exactly today's configuration.
5. **Observability.** `_on_committed` emits one INFO line for each transcript it forwards to `on_committed`, in both modes:

   `Azure STT committed lang=<locale> commit_lag_ms=<int> chars=<int>`

   - `lang` is the detected locale in LID mode (`AutoDetectSourceLanguageResult(evt.result).language`) and the configured locale in single-locale mode.
   - `commit_lag_ms` is the wall-clock time from the most recent `recognizing` event of the same phrase to the `recognized` event, or `-1` when no interim result was seen. It gives a comparable latency signal in both modes. The single-locale run supplies the baseline.
   - Transcript text is never logged by this line (it may contain health data). `on_committed` keeps its `Callable[[str], None]` contract.
6. The startup log line names the mode: `AzurePersistentStt started (lang=fi-FI)` stays as is for single-locale mode; LID mode logs `AzurePersistentStt started (lid=continuous langs=fi-FI,sv-SE,en-GB)`.

**Not changed:** the ElevenLabs and mock providers; `language_code`'s default; echo discard; reconnect backoff and limits; `on_committed`, `on_recognizing` and `on_error` signatures; TTS.

## Acceptance criteria

- [ ] AC-01: A test-only RED commit adds `tests/test_vr009_azure_stt_language_id.py`. Failures are assertions against current behaviour, not import, collection or fixture errors. Every new test carries `@pytest.mark.req("VR-009")`.
- [ ] AC-02: `_parse_languages` returns `()` for `None`, `""` and `" , "`, and returns `("fi-FI", "sv-SE", "en-GB")` for `" fi-FI, sv-SE ,en-GB "`. It raises `ValueError` for:
  - `"fi"` (no region);
  - `"fi_FI"` (wrong separator);
  - `"en-US,en-GB"` (duplicate base language);
  - 11 distinct valid locales.
- [ ] AC-03: With `AZURE_STT_LANGUAGES` unset and no `languages` argument, the recognizer is built with `SpeechConfig(subscription=..., region=...)`, `speech_recognition_language == "fi-FI"`, and no `auto_detect_source_language_config`. The same holds after `_reconnect()`. The existing tests in `tests/test_azure_stt.py` pass without weakened assertions.
- [ ] AC-04: With `languages=("fi-FI", "sv-SE", "en-GB")`:
  - `SpeechConfig` is built from `endpoint="wss://swedencentral.stt.speech.microsoft.com/speech/universal/v2"` (region injected as `swedencentral`) and the subscription key;
  - `SpeechServiceConnection_LanguageIdMode` is set to `"Continuous"`;
  - `speech_recognition_language` is not set;
  - `SpeechRecognizer` receives an `AutoDetectSourceLanguageConfig` whose languages are exactly those three, in that order.
- [ ] AC-05: AC-04's configuration is rebuilt identically by `_reconnect()`. The test asserts the second recognizer's construction arguments, not just that a recognizer exists.
- [ ] AC-06: In LID mode a committed result whose detected language is `sv-SE` calls `on_committed` with the stripped text and logs `lang=sv-SE` with an integer `commit_lag_ms` and `chars` equal to the stripped length. The log record does not contain the transcript text. The echo-discard cases (speaking, discard window) still drop the result and log no `committed` line.
- [ ] AC-07: The module-level env path is tested with `importlib.reload` under monkeypatched env:
  - `AZURE_STT_LANGUAGES=fi-FI,sv-SE,en-GB` → a default-constructed provider is in LID mode;
  - `AZURE_STT_LANGUAGES=fi` → `ValueError` at reload, which is import time.
- [ ] AC-08: One live probe outside the unit suite, recorded in this FR: with a real key, a script feeds an 8 kHz μ-law Swedish clip and a Finnish clip through `AzurePersistentStt(languages=("fi-FI","sv-SE","en-GB"))`. The probe records:
  - the region used;
  - each committed line's `lang`, `commit_lag_ms` and text;
  - the same Finnish clip in single-locale mode as a `commit_lag_ms` baseline.

  A `Canceled` error from the v2 endpoint is a valid result. If it occurs, record it and stop before release.
- [ ] AC-09: The full offline suite passes (`pytest tests/`). Version metadata and `CHANGELOG.md` record VR-009. The sdist and wheel build, `twine check` passes, and a clean non-editable wheel install constructs the provider in LID mode from `AZURE_STT_LANGUAGES`. Tagging and publishing need the owner's explicit "publish", as for VR-007.
- [ ] AC-10: The FR records implementation decisions, RED/GREEN commits, suite results, the AC-08 probe output and any deviation. Consumer wiring and field acceptance stay assigned to CSAP NC-566.

## Research and alternatives

| Alternative | Evidence | Disposition |
|---|---|---|
| No locale at all ("any language") | Fast-transcription multilingual model is file-based REST and lists 15 locales, none `fi`/`sv` (Microsoft Learn, fetched 2026-10-28) | Rejected: not streaming and does not cover the core languages |
| At-start LID (default mode, ≤4 candidates) | Fact 4; one locale is fixed for the whole call after the first seconds | Rejected for the first iteration: a caller who opens in Finnish and switches to Swedish ("på svenska, tack") stays Finnish. Can be revisited if NC-566 shows continuous LID latency is unacceptable |
| Extend the callback to `on_committed(text, lang)` | Changes the public callback contract for all consumers; no consumer acts on language yet | Deferred: CSAP NC-566 only needs observation. A later FR adds a typed result when the graph consumes language |
| Switch CSAP to ElevenLabs STT auto-detect | Unverified for realtime streaming on phone audio; would also move the Finnish baseline off Azure | Not chosen here; separate probe if Azure LID fails AC-08 or NC-566 |
| Consumer-side env parsing (CSAP reads the list and passes `languages=`) | `session.stt_factory()` is called with no arguments at `twilio_ws.py:245`; wiring arguments would change the transport | Rejected: the library already reads its own `AZURE_*` env names (lines 31-32, `azure_tts.py:25`) |

**Is this a graph?** No. Provider configuration, no LLM stage.

## Out of scope

- Choosing the reply language, TTS voice, prompts, or predefined audio by detected language (CSAP research Options 2b/2c).
- Sámi: not an Azure STT, TTS or LID locale.
- `sv-FI`: no Azure STT locale exists; `sv-SE` is the only Swedish candidate.
- ElevenLabs provider changes.

## Source references

- `voice_runtime/providers/azure_stt.py:31-32` — module-level env reads
- `voice_runtime/providers/azure_stt.py:53,58` — `language_code` argument
- `voice_runtime/providers/azure_stt.py:98-102` — `SpeechConfig` and locale in `start()`
- `voice_runtime/providers/azure_stt.py:241-281` — `_reconnect()` duplicate recognizer build
- `voice_runtime/providers/azure_stt.py:283-313` — `_on_committed`, `_on_recognizing`
- `voice_runtime/transports/twilio_ws.py:245` — `session.stt_factory()` without arguments
- `tests/test_azure_stt.py` — existing provider tests
- CSAP `server_fsm.py:126`, `services/persistent_stt.py`, `infra/kustomize/overlays/gcp_test/csap.test.environment:19` (`AZURE_SPEECH_REGION=swedencentral`)
