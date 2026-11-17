# VR-015 evidence: Google STT speech-activity pulse

- **Date:** 2026-11-17
- **FR:** [VR-015](../VR-015-google-stt-speech-activity-pulse.md); judgement [VR-015 judgement](../VR-015-google-stt-speech-activity-pulse.judgement.md)
- **Code:** voice_runtime 0.6.1 working tree on branch `docs/vr-015-google-stt-pulse`, based on `d21ef0b` (the FR commits). Python 3.14 checkout venv, editable install.
- **SDKs:** azure-cognitiveservices-speech 1.51.2, google-cloud-speech 2.41.0, google-cloud-texttospeech 2.38.0.

## AC-01: RED on the unchanged provider

Test module `tests/test_vr015_google_stt_pulse.py` against voice_runtime 0.6.0 `google_stt.py`:

```bash
.venv/bin/python -m pytest tests/test_vr015_google_stt_pulse.py -q -p no:cacheprovider
```

Result: **17 failed, 1 passed**. The module collected and ran with the Google extra installed. Every failure was an assertion about missing pulses, for example `assert [0.0] == [0.0, 0.25, 0.5]` (8 times), `assert 1 == 120` (cap), `assert [0.0, 0.1] == [0.0, 0.1, 0.35, 0.6, 0.85]` (restart), or the missing `SPEECH_PULSE_INTERVAL_S` constant. No import, fixture or skip failure. The one pass, `test_no_pulse_without_begin`, holds on 0.6.0 by design.

## AC-02 to AC-08: GREEN

```bash
.venv/bin/python -m pytest tests/test_vr015_google_stt_pulse.py tests/test_vr013_google_stt.py -q -p no:cacheprovider --no-cov
```

Result: **103 passed** (18 VR-015, 85 VR-013), after one test-side correction: the AC-04 test compared the "Kyllä." commit time against a reference point set after it. It now asserts the order of commits and the second commit at `t=2.1`.

| AC | Test |
|---|---|
| AC-02 | `test_begin_then_pulse_every_quarter_second` |
| AC-03 | `test_speech_end_stops_pulse`, `test_final_without_end_stops_pulse[committed, empty, speaking_suppressed, echo_discarded]` |
| AC-04 | `test_continuation_after_final_is_pulsed_until_speech_end` |
| AC-05 | `test_pulse_stops_at_cap_with_one_warning`, `test_cap_constants` |
| AC-06 | `test_second_begin_restarts_single_schedule` |
| AC-07 | `test_speaking_suppresses_then_pulses_resume_inside_echo_window`, `test_interim_text_forwarded_and_pulse_continues`, `test_no_pulse_without_begin` |
| AC-08 | `test_stop_during_pulse_leaves_nothing_pending`, `test_input_eof_ends_pulse`, `test_terminal_failure_ends_pulse`, `test_retry_ends_pulse_until_begin_on_new_stream`, `test_forced_rotation_ends_pulse_until_begin_on_new_stream` |

## AC-09: full suite and checks

```bash
.venv/bin/python -m pytest tests -q -p no:cacheprovider -o addopts=""
.venv/bin/ruff check voice_runtime/
.venv/bin/ruff format --check voice_runtime tests
.venv/bin/python -m build
```

- pytest: **907 passed, 1 skipped**. The skip is `tests/integration/test_stt_scribe_behavior.py` (`ELEVENLABS_API_KEY required`), not Google coverage. `-o addopts=""` matches CI (`publish.yml`: `pytest tests/ -q`): run from the checkout, pytest otherwise inherits the parent yamlgraph `pyproject.toml` coverage gate for the `yamlgraph` package, which reports 0 %.
- `ruff check voice_runtime/`: all checks passed.
- `ruff format --check`: the VR-015 files are formatted. Two untouched files (`tests/test_nc340_stt_feed_survives_reconnect.py`, `tests/test_vr005_stt_session_task_ownership.py`) fail the format check identically before this change; they are outside VR-015 scope.
- Build: `voice_runtime-0.6.1-py3-none-any.whl` and `voice_runtime-0.6.1.tar.gz`.
- No existing test file was edited.

### Re-run after the module split

The pulse moved to `_google_pulse.py` and error classification to `_google_errors.py` (FR decision, file-size gate). Code identical in behaviour; AC-10 not repeated.

- `google_stt.py`: 493 → 437 lines; `_google_pulse.py` 62, `_google_errors.py` 46.
- VR-015 + VR-013 tests: **103 passed**.
- Full suite (`-o addopts=""`): **907 passed, 1 skipped** (same ElevenLabs skip).
- `pre-commit run --files` on all VR-015 files, with `.venv/bin` on `PATH` for the radon hook: every hook passed, including the file-size gate, the radon CC gate and pytest.
- Build: `voice_runtime-0.6.1-py3-none-any.whl`, `voice_runtime-0.6.1.tar.gz`.

## AC-10: single live desk check (owner-authorized 2026-11-17)

One invocation, no retries, synthetic text only, no configuration or deployment change. Before the run, the ADC token was checked (`gcloud auth application-default print-access-token`), so the single attempt could not be spent on a credentials failure.

Effective non-secret settings (CSAP `.env` keys loaded, values not recorded):

```text
AZURE_SPEECH_REGION=swedencentral
GOOGLE_CLOUD_PROJECT=scp-tenant-csap-test
GOOGLE_SPEECH_LOCATION=eu
GOOGLE_SPEECH_PROJECT=scp-tenant-csap-test
GOOGLE_STT_ENDPOINTING=supershort
GOOGLE_STT_LANGUAGE=fi-FI
GOOGLE_STT_MODEL=chirp_3
STT_LANGUAGE_CODE=fi
```

Command (`$P` is the Run 3/4 paragraph of CSAP `feature-requests/evidence/2026-11-16-chirp3-speech-check.md`):

```bash
P='Soitan, koska minulla on ollut kuumetta ja kurkkukipua kolme päivää. Kuume oli eilen illalla kolmekymmentäkahdeksan ja puoli astetta. Otan verenpainelääkettä, ja reseptini on loppumassa ensi viikolla. Voisiko hoitaja soittaa minulle takaisin iltapäivällä?'
.venv/bin/python -m voice_runtime.speech_check --provider azure --stt-provider google --text "$P" --timeout 45
```

Full output (2026-11-17T08:34:46Z). The header `sha` is the branch HEAD; the provider change was in the working tree, not yet committed.

```text
version=0.6.1 file=/Users/sami.j.p.heikkinen/src/yamlgraph/projects/voice_runtime/voice_runtime/speech_check.py sha=d21ef0bd46a974b91bfd25714547013c917b4ed1 sdk={'azure-cognitiveservices-speech': '1.51.2', 'elevenlabs': '2.62.0', 'google-cloud-speech': '2.41.0', 'google-cloud-texttospeech': '2.38.0'} stt_language_code=fi google_project=scp-tenant-csap-test google_location=eu
provider | stt_provider | step | status | first_audio_ms | total_ms | commits | lag_ms | wer | detail
INFO:voice_runtime.providers.azure_tts:Speaking (Azure): Soitan, koska minulla on ollut kuumetta ja kurkkukipua kolme päivää. Kuume oli e
INFO:voice_runtime.providers.azure_tts:Spoke (Azure): Soitan, koska minulla on ollut kuumetta ja kurkkuk (1.00s)
azure | - | tts | PASS | 785 | 1048 | - | - | - | bytes=155300 audio_s=19.413 recognizing=- keys=last_spoken
INFO:voice_runtime.providers.google_stt:GooglePersistentStt started (lang=fi-FI model=chirp_3 location=eu)
INFO:voice_runtime.providers.google_stt:Google STT stream open
INFO:voice_runtime.providers.google_stt:Google STT committed lang=fi-FI commit_lag_ms=500 chars=30
INFO:voice_runtime.providers.google_stt:Google STT committed lang=fi-FI commit_lag_ms=528 chars=32
INFO:voice_runtime.providers.google_stt:Google STT committed lang=fi-FI commit_lag_ms=521 chars=32
INFO:voice_runtime.providers.google_stt:Google STT committed lang=fi-FI commit_lag_ms=436 chars=12
INFO:voice_runtime.providers.google_stt:Google STT committed lang=fi-FI commit_lag_ms=509 chars=24
INFO:voice_runtime.providers.google_stt:Google STT committed lang=fi-FI commit_lag_ms=547 chars=41
INFO:voice_runtime.providers.google_stt:Google STT committed lang=fi-FI commit_lag_ms=570 chars=54
INFO:voice_runtime.providers.google_stt:GooglePersistentStt stopped
azure | google | stt | PASS | - | 26638 | 7 | 202 | 0.156 | bytes=- audio_s=- recognizing=63 keys=- transcript='Soitan, koska minulla on ollut kuumetta ja kurkkukipua 3 päivää Hyvää. Kuume oli eilen illalla 3 18,5 astetta Otan verenpainelääkettä. Ja reseptini on loppumassa ensi viikolla. Voisiko hoitaja soittaa minulle takaisin iltapäivällä?'
exit=0
```

| Gate | Required | Result |
|---|---|---|
| Exit code | 0 | 0 |
| `recognizing` | ≥ 20 (0.6.0 baseline 7) | **63** |
| WER | ≤ 0.156 (Azure TTS → Google STT row, evidence line 105) | **0.156** |

**PASS.** 63 events over 19.4 s of audio is about 3.2 per second, close to the 4 per second pulse rate during speech. Commit lags of 436–570 ms match the END → final latency accepted as limit 1. The transcript shows the same kind of recognition errors as the 0.6.0 baseline ("3 18,5 astetta" here, "3 28,5" then): this is a recognition-accuracy matter, outside VR-015, and this desk check does not certify CSAP calls or clinical accuracy.
