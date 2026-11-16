# VR-014 AC-10: live speech-check run (Azure, ElevenLabs)

- **Date:** 2026-11-16
- **Approval:** VR-014 Q1 (owner, 2026-11-16). Synthetic text only: the default sentence.
- **Code:** voice_runtime 0.5.0 at `1d1892d` (VR-014 GREEN plus the version fix), Python 3.14 checkout venv.
- **SDKs:** azure-cognitiveservices-speech 1.51.2, elevenlabs 2.62.0.
- **Environment:** CSAP `.env` keys (values not recorded), `AZURE_SPEECH_REGION=swedencentral`, `STT_LANGUAGE_CODE=fi` set explicitly. ElevenLabs TTS used the library default voice `21m00Tcm4TlvDq8ikWAM` with `eleven_multilingual_v2`; Azure TTS used `AZURE_TTS_VOICE` from the CSAP `.env`.

## Result

5 of 6 pairs PASS. **ElevenLabs TTS → ElevenLabs STT FAIL** (WER 0.25 > 0.2).

**Owner decision (2026-11-16, Sami Heikkinen): the FAIL is recorded and accepted** as a finding about the ElevenLabs default voice, not a defect in the tool. It does not block release.

## Command 1 (exit 1)

`python -m voice_runtime.speech_check --provider azure --provider elevenlabs --mode roundtrip --out tmp/vr014`

```text
provider | stt_provider | step | status | first_audio_ms | total_ms | commits | lag_ms | wer | detail
azure | - | tts | PASS | 942 | 1132 | - | - | - | bytes=37200 audio_s=4.650 recognizing=- keys=last_spoken
azure | azure | stt | PASS | - | 10332 | 1 | 279 | 0.000 | bytes=- audio_s=- recognizing=9 keys=- transcript='Hyvää huomenta. Haluaisin varata ajan lääkärille ensi viikolla.'
elevenlabs | - | tts | PASS | 1583 | 1620 | - | - | - | bytes=28607 audio_s=3.576 recognizing=- keys=last_spoken
elevenlabs | elevenlabs | stt | FAIL | - | 9655 | 1 | 1778 | 0.250 | reason=wer 0.250 > 0.2 bytes=- audio_s=- recognizing=4 keys=- transcript='Hyvää huomenta! Mä haluaisin varata ajan lääkärille ens viikolla.'
version=0.5.0 file=/Users/sami.j.p.heikkinen/src/yamlgraph/projects/voice_runtime/voice_runtime/speech_check.py sha=1d1892d477b7d1b1732d8a2c93272605ddc22f22 sdk={'azure-cognitiveservices-speech': '1.51.2', 'elevenlabs': '2.62.0', 'google-cloud-speech': '2.41.0', 'google-cloud-texttospeech': '2.38.0'} stt_language_code=fi
```

## Command 2 (exit 0)

`python -m voice_runtime.speech_check --provider azure --mode roundtrip --stt-provider elevenlabs`

```text
provider | stt_provider | step | status | first_audio_ms | total_ms | commits | lag_ms | wer | detail
azure | - | tts | PASS | 628 | 775 | - | - | - | bytes=37200 audio_s=4.650 recognizing=- keys=last_spoken
azure | elevenlabs | stt | PASS | - | 10895 | 1 | 1146 | 0.000 | bytes=- audio_s=- recognizing=5 keys=- transcript='Hyvää huomenta! Haluaisin varata ajan lääkärille ensi viikolla.'
```

## Command 3 (exit 0)

`python -m voice_runtime.speech_check --provider elevenlabs --mode roundtrip --stt-provider azure`

```text
provider | stt_provider | step | status | first_audio_ms | total_ms | commits | lag_ms | wer | detail
elevenlabs | - | tts | PASS | 753 | 790 | - | - | - | bytes=27492 audio_s=3.437 recognizing=- keys=last_spoken
elevenlabs | azure | stt | PASS | - | 8958 | 1 | 871 | 0.000 | bytes=- audio_s=- recognizing=10 keys=- transcript='Hyvää huomenta. Haluaisin varata ajan lääkärille ensi viikolla.'
```

## Diagnosis of the FAIL

The ElevenLabs audio that command 1 captured (`tmp/vr014/elevenlabs.wav`) was given to Azure STT with the default sentence as reference:

`python -m voice_runtime.speech_check --provider azure --mode stt --audio tmp/vr014/elevenlabs.wav --text "Hyvää huomenta, haluaisin varata ajan lääkärille ensi viikolla."`

```text
provider | stt_provider | step | status | first_audio_ms | total_ms | commits | lag_ms | wer | detail
azure | azure | stt | FAIL | - | 8948 | 1 | 892 | 0.250 | reason=wer 0.250 > 0.2 bytes=- audio_s=- recognizing=9 keys=- transcript='Hyvää huomenta. Mä haluaisin varata ajan lääkärille ens viikolla.'
```

Azure heard exactly the same words ("Mä" added, "ens" instead of "ensi"). So the **voice spoke colloquial Finnish**; the ElevenLabs recogniser transcribed it correctly. In command 3, the same voice read the sentence correctly, so ElevenLabs synthesis varies from run to run with this default voice. Google rows are a follow-up, not a gate.
