# VR-009 research record — Azure STT language identification

**Date:** 2026-10-28
**FR:** [VR-009](../VR-009-azure-stt-language-identification.md)
**Consumer research:** CSAP `docs/2026-10-28-i18n-vs-finnish-bindings-research.md` (committed with CSAP NC-566). This file restates the parts VR-009 depends on so the voice_runtime record stands alone.

## 1. Field incident (sanitized)

CSAP TEST, 2026-10-28, persona case HP-41 (synthetic Swedish-speaking caller, csap-black-box-tests), SUT call `CA6a6f98bb19c2fbd60d2ca77cac84277e`, CSAP git `9715f56d`. The `fi-FI` recognizer turned the Swedish speech into Finnish-shaped nonsense. A recap approval was recorded from a garbled reply and the generated ticket carried unusable background text. The harness reported PASS because HP-41 is informational. The caller is an LLM persona; no patient or real caller data is involved.

## 2. Provider constraints

Source: Microsoft Learn, "Implement language identification", <https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-identification>, page updated 2026-08-10, fetched 2026-10-28.

| # | Constraint | Wording relied upon |
|---|---|---|
| P-1 | Candidate limits | At-start LID: up to 4 candidate languages; continuous LID: up to 10. |
| P-2 | Locale form | Candidates are full locales with a dash; one locale per base language (e.g. not `en-US` and `en-GB` together). |
| P-3 | Closed set | The service returns one of the candidates even when the spoken language is not among them. |
| P-4 | Continuous mode | Set `SpeechServiceConnection_LanguageIdMode` to `"Continuous"`; works with continuous recognition only; no switch detection inside one sentence. |
| P-5 | Endpoint | "Currently for speech to text recognition with continuous language identification, you must create a SpeechConfig from endpoint." Sample endpoint: `wss://<region>.stt.speech.microsoft.com/speech/universal/v2`. |
| P-6 | Result | Detected locale per result: `speechsdk.AutoDetectSourceLanguageResult(result).language`; a `NoMatch` result may carry `Unknown`. |
| P-7 | Latency | "For speech recognition, the initial latency is higher with language identification." |

Also checked the same day: the fast-transcription multilingual model is file-based REST and its locale list contains neither Finnish nor Swedish. `sv-FI` is not an Azure STT locale; Sámi is not an Azure STT, TTS or LID locale.

## 3. SDK probe

Command (CSAP venv, 2026-10-28):

```bash
.venv/bin/python -c "
import azure.cognitiveservices.speech as s, inspect
print(s.__version__ if hasattr(s,'__version__') else 'n/a')
print(hasattr(s.languageconfig,'AutoDetectSourceLanguageConfig'), s.PropertyId.SpeechServiceConnection_LanguageIdMode)
print(inspect.signature(s.SpeechConfig.__init__))
print(hasattr(s,'AutoDetectSourceLanguageResult'))
"
```

Output:

```text
1.51.0
True PropertyId.SpeechServiceConnection_LanguageIdMode
(self, subscription: str | None = None, region: str | None = None, endpoint: str | None = None, host: str | None = None, auth_token: str | None = None, speech_recognition_language: str | None = None, token_credential: ForwardRef('TokenCredential') | None = None, key_credential: ForwardRef('AzureKeyCredential') | None = None)
True
```

voice_runtime floors the SDK at `azure-cognitiveservices-speech>=1.38.0`.

Not verified offline: continuous LID in `swedencentral`, accuracy on 8 kHz μ-law phone audio and short answers, the added latency. AC-08 (live probe) and CSAP NC-566 (field test) measure these.

## 4. Solution classes

| # | Class | Disposition |
|---|---|---|
| S-1 | No locale ("any language") recognition | Rejected: only file-based fast transcription offers it, and its list lacks fi/sv (§2). |
| S-2 | At-start LID (≤4 candidates, language fixed after the first seconds) | Deferred: a caller who opens in Finnish and then switches stays Finnish (P-4). Revisit if continuous LID latency is unacceptable. |
| S-3 | Continuous LID in the provider, opt-in via `AZURE_STT_LANGUAGES` | **Chosen.** Smallest change at the provider boundary; unset keeps today's path. |
| S-4 | Consumer-side parsing, consumer passes `languages=` | Rejected: the transport calls `session.stt_factory()` with no arguments (`twilio_ws.py:245`); the library already reads its own `AZURE_*` env names. |
| S-5 | Switch the consumer to ElevenLabs STT language auto-detect | Not chosen: unverified for realtime phone audio and moves the Finnish baseline off Azure. Separate probe if S-3 fails. |
| S-6 | Extend the callback to `on_committed(text, lang)` | Deferred: changes the public callback contract; no consumer acts on language yet. |

## 5. Prior art dispositions

- **NC-161** (Azure persistent STT): introduced the single-locale provider; VR-009 keeps its default path unchanged.
- **NC-258** (reconnect): `_reconnect()` rebuilds the recognizer; VR-009 routes both builds through one `_build_recognizer()` so reconnect cannot drop LID.
- **VR-005** (task ownership): untouched; VR-009 adds no tasks or futures.

## 6. Is this a graph?

No. Provider configuration with no LLM stage.
