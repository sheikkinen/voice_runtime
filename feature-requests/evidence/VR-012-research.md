# VR-012 research: Google Chirp 3 speech-to-text and text-to-speech

Evidence for [VR-012](../VR-012-google-chirp-stt-tts-providers.md). All probes ran from the owner's workstation against GCP project `scp-tenant-csap-test`, using the owner's gcloud user credentials and that project as the quota project. All audio was synthetic: Chirp or Azure text-to-speech output, or the `csap-black-box-tests` synthetic persona. No caller audio was used.

## 1. Region: the `eu` multi-region is reachable (2026-11-13)

CSAP NC-567 (2026-10-29) ended "stop-refused": the organisation policy `constraints/gcp.resourceLocations` blocked the `eu` location, and `europe-west3` no longer offered Chirp 3 for `fi-FI`. On 2026-11-13 a request to `eu` succeeded:

```bash
curl -sS -X POST \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "x-goog-user-project: $P" -H "Content-Type: application/json" \
  "https://eu-speech.googleapis.com/v2/projects/$P/locations/eu/recognizers/_:recognize" \
  -d @req.json   # {"config":{"autoDecodingConfig":{},"model":"chirp_3","languageCodes":["fi-FI"]},"content":"<base64>"}
```

- A 44.1 kHz PCM WAV gave a correct transcript in about 2.2 s.
- 55 s of 8 kHz μ-law synthetic persona audio (`app/logs/20260810_120425.wav`) gave good Finnish, with "ramipriilin" correct. Errors: "Terveystalo" was heard as "Tervolan", a stray "30 8" appeared, and the transcript ended early.
- It is not yet recorded **why** `eu` now passes, i.e. whether the policy changed or an exception was added. That is tracked in Jira VBOT-211, not here.

API enablement as of 2026-11-13:

| Project | `speech.googleapis.com` | `texttospeech.googleapis.com` |
|---|---|---|
| `scp-tenant-csap-test` | enabled | enabled manually by the owner on 2026-11-13 |
| `scp-tenant-csap-stg` | disabled | disabled |
| `scp-tenant-csap-prod` | not visible (`AUTH_PERMISSION_DENIED`) | not visible |

Neither API is declared in CSAP's Terraform (VBOT-211).

## 2. Text-to-speech, non-streaming (2026-11-13)

- `GET https://eu-texttospeech.googleapis.com/v1/voices?languageCode=fi-FI` lists 30 `fi-FI-Chirp3-HD-*` voices.
- `POST /v1/text:synthesize` with `audioConfig {"audioEncoding":"MULAW","sampleRateHertz":8000}` returns a **WAV file with a header**, not raw μ-law. It took about 0.9 s per greeting sentence for both `Achernar` and `Charon`.
- Feeding both outputs back through Chirp 3 speech-to-text reproduced the text exactly, including "Terveystalon".

## 3. SDK surface (2026-11-16, Python 3.14, clean venv)

The installed versions were `google-cloud-speech==2.41.0` and `google-cloud-texttospeech==2.38.0`.

```text
StreamingAudioConfig fields: ['audio_encoding', 'sample_rate_hertz', 'speaking_rate']
AudioEncoding: [..., 'LINEAR16', 'MP3', 'OGG_OPUS', 'MULAW', 'ALAW', 'PCM', 'M4A']
StreamingRecognitionFeatures: ['enable_voice_activity_events', 'interim_results', 'voice_activity_timeout', 'endpointing_sensitivity']
EndpointingSensitivity: [UNSPECIFIED, STANDARD, SUPERSHORT, SHORT]
SpeechEventType: [UNSPECIFIED, END_OF_SINGLE_UTTERANCE, SPEECH_ACTIVITY_BEGIN, SPEECH_ACTIVITY_END]
```

## 4. Live streaming probe (2026-11-16)

**Text-to-speech.** `TextToSpeechClient.streaming_synthesize` against `eu-texttospeech.googleapis.com`, voice `fi-FI-Chirp3-HD-Charon`, with `StreamingAudioConfig(audio_encoding=MULAW, sample_rate_hertz=8000)` and the text "Hei. Haluaisin uusia reseptin. Lääke on ramipriili, viisi milligrammaa.":

```text
TTS stream MULAW8k: chunks=34 bytes=61125 (7.64s) first_chunk=0.48s total=1.07s head=b'\xff\xff\x7f\xff'
```

The streaming output is **raw μ-law with no WAV header**. The first chunk arrived in 0.48 s.

**Speech-to-text.** The text-to-speech output above, followed by 2 s of μ-law silence, was sent to `SpeechClient.streaming_recognize` against `eu-speech.googleapis.com`. Settings:

- recognizer `projects/scp-tenant-csap-test/locations/eu/recognizers/_`;
- `ExplicitDecodingConfig(MULAW, 8000, 1)`, model `chirp_3`, `fi-FI`;
- `interim_results=True`, `enable_voice_activity_events=True`, `endpointing_sensitivity=SUPERSHORT`;
- 800-byte (100 ms) requests sent every 100 ms.

Output (the first column is seconds since the stream started; speech in the stream ends at 7.64 s):

```text
   0.84s event SPEECH_ACTIVITY_BEGIN
   1.71s event SPEECH_ACTIVITY_END
   2.16s final=True 'Hei.'
   2.16s event SPEECH_ACTIVITY_BEGIN
   4.01s event SPEECH_ACTIVITY_END
   4.61s final=True ' Haluaisin uusia reseptin.'
   4.61s event SPEECH_ACTIVITY_BEGIN
   6.12s event SPEECH_ACTIVITY_END
   6.68s final=True ' Lääke on ramipriili.'
   7.20s event SPEECH_ACTIVITY_BEGIN
   8.68s event SPEECH_ACTIVITY_END
   9.12s final=True ' 5 mg'
```

Findings:

1. **No interim results arrived**, although `interim_results=True` was requested. Only final results and voice-activity events came back.
2. Each final result arrived 0.45–0.60 s after its `SPEECH_ACTIVITY_END`.
3. Results carry a leading space, so the provider must strip text.
4. Numbers are written as digits: "viisi milligrammaa" became "5 mg". Azure does the same with its default formatting; the consumer's prompts already handle digits.
5. With SUPERSHORT endpointing, each sentence of one turn became a separate final result. CSAP's FSM already accumulates `transcribed` events (NC-203), so this matches the current Azure behaviour, where one turn can also produce several commits.

## 5. Google limits relied on (docs, fetched 2026-11-16)

Sources: <https://docs.cloud.google.com/speech-to-text/docs/models/chirp-3>, <https://cloud.google.com/speech-to-text/v2/quotas>, <https://cloud.google.com/text-to-speech/docs/create-audio-text-streaming>.

- **Streaming speech-to-text:** each streaming request carries at most 25 KB of audio, a stream lasts at most 5 minutes, and audio is expected at about real-time pace. Longer sessions need the documented "endless streaming" restart.
- **Quotas:** 300 concurrent streaming sessions per region and 3,000 requests per minute.
- **Chirp 3:**
  - Supports `Recognize`, `StreamingRecognize` and `BatchRecognize`.
  - `fi-FI` is GA in the `eu` multi-region.
  - Speech adaptation (phrase biasing) is GA.
  - Utterance-level timestamps are available only in streaming; diarization only in batch.
  - Word-level timestamps and confidence are not supported.
- **Streaming text-to-speech:** works only with Chirp 3 HD voices. The first request carries `StreamingSynthesizeConfig`; the following requests carry text.

## 6. Prior art dispositions

| Item | Disposition |
|---|---|
| CSAP NC-160 (Google Cloud speech provider) | Superseded. It was written for the per-turn speech-to-text mode that NC-166 removed, and it was never judged. |
| CSAP NC-567 (Chirp 3 feasibility spike, `scripts/spike_chirp3_stt.py`) | Its `eu` block no longer holds (§1). Its 56-clip comparison against Azure is still the right *quality* gate, but that gate belongs to the CSAP consumer FR, not this library FR. |
| voice_runtime NC-161/NC-258, VR-005, VR-009 (`AzurePersistentStt`) | Structural template: one recogniser builder shared by start and reconnect, backoff reconnect, owned tasks, echo discard, and a commit log line without text. |
| `scripts/probe_vr009_lid.py` | Pattern for the command-line tester: 20 ms frames, trailing silence, a log-capture handler, and a version/SHA header. |
