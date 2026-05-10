# NC-159: ElevenLabs Native mulaw — Eliminate ffmpeg

**Status:** Approved
**Date:** 2026-03-19
**Ref:** `docs/voice-runtime-provider-alternatives.md` (Section 2)

## Judgement

**Verdict: APPROVED — straightforward deletion with verified SDK support.**

The ElevenLabs SDK confirms `ulaw_8000` as a first-class `output_format`
value (verified in `types/text_to_speech_output_format_enum.py`,
`types/allowed_output_formats.py`, `types/tts_output_format.py`).
The current MP3→ffmpeg→mulaw pipeline is provably redundant.

**Verified claims:**
- `elevenlabs_tts.py` is 128 lines; ~50 lines are ffmpeg plumbing. Accurate.
- Both ninchat_voice (`services/tts.py`) and outcaller (`nodes/tts.py`)
  delegate to `voice_runtime.providers.elevenlabs_tts.ElevenLabsTTS`. Fix
  is genuinely in one place.
- `scripts/generate_predefined_audio.py` still needs ffmpeg. "Not in scope:
  Removing ffmpeg from Dockerfile" is correct.

**Amendments:**
1. **Test count correction:** 4 of 6 tests mock `subprocess.Popen`, not 3.
   The FR lists `test_speak_calls_elevenlabs_and_ffmpeg`, `test_barge_in_interrupts_playback`,
   and implicitly a third — but `test_speak_accepts_voice_id_override` and
   `test_mark_timeout_does_not_raise` also mock it. All 4 need mock removal.
2. **Outcaller test impact:** `outcaller/tests/unit/test_outcaller_tts.py`
   also mocks `subprocess.Popen` for the ffmpeg pipeline. This test must
   be updated too (remove Popen mock, make convert() return mulaw bytes
   directly). The FR claims "All outcaller tests pass (211)" but doesn't
   mention this test needs changes. Correct outcaller test count is 189,
   not 211.
3. **Option A (stream chunks as-is) is correct.** The transport's
   `put_outbound_sync` → base64 encode → Twilio already handles arbitrary
   sizes. No reframing needed.
4. **`import subprocess` removal:** Also remove `import threading` from
   elevenlabs_tts.py — it's only used for the `_feed_mp3` thread. The
   `threading.Event` type annotation for `stop_event` comes from the caller,
   not from this module's import. Wait — `stop_event: threading.Event | None`
   is in the function signature, so the import stays for the type hint.
   Correction: keep `import threading`.
5. **`output_format` value:** Verify whether the SDK enum uses `"ulaw_8000"`
   (string) or an enum member. The `convert()` method accepts string values
   per SDK source. Using `output_format="ulaw_8000"` is correct.

**Authority granted.** Small, well-scoped deletion. Execute with TDD:
write test expecting no subprocess.Popen call first, then remove ffmpeg code.

## Problem

`voice_runtime/providers/elevenlabs_tts.py` requests MP3 from ElevenLabs
and pipes it through an ffmpeg subprocess to convert to mulaw 8kHz:

```
ElevenLabs API → MP3 stream → ffmpeg subprocess → mulaw 8kHz → session queue
                               ↑ feed thread      ↑ stdout pipe
```

ElevenLabs natively supports `output_format="ulaw_8000"` — raw G.711
mulaw at 8kHz, exactly what Twilio Media Streams expects. The ffmpeg
conversion is unnecessary. It was inherited from the original ninchat_voice
and outcaller implementations which pre-date ElevenLabs adding mulaw support.

## Impact of current approach

- **Deployment dependency:** ffmpeg must be installed in every container
- **Latency:** transcoding adds delay between API response and audio playback
- **Complexity:** 50 lines of subprocess + feed thread + pipe management
- **Failure modes:** ffmpeg crash, broken pipe, stdin/stdout race conditions
- **Resource usage:** extra process per TTS call

## Change

### elevenlabs_tts.py: one parameter + deletion

**Before (lines 76-119):** subprocess.Popen + feed thread + pipe read loop

**After:**

```python
audio_stream = client.text_to_speech.convert(
    voice_id=self._voice_id,
    model_id=self._model_id,
    text=text,
    output_format="ulaw_8000",
)
for chunk in audio_stream:
    if not chunk:
        continue
    if stop_event and stop_event.is_set():
        logger.info("Barge-in interrupt")
        return {"last_spoken": text, "interrupted": True}
    session.put_outbound_sync(chunk)
    session.tap_agent(chunk)
```

**What gets deleted:**
- `import subprocess` (line 13)
- `subprocess.Popen(["ffmpeg", ...])` block (lines 76-88)
- `_feed_mp3()` inner function + thread (lines 90-104)
- `proc.stdout.read()` loop replaced by direct iteration (lines 107-116)
- `feed_thread.join()` + `proc.wait()` cleanup (lines 118-119)
- `proc.terminate()` on barge-in (line 113)

**What stays unchanged:**
- Constructor (`__init__`)
- Early returns (empty text, disconnected session)
- `session.put_outbound_sync()` + `session.tap_agent()` calls
- `session.send_mark_and_wait("tts_complete")` at end
- Return value contract (`{"last_spoken": text}` etc.)

### Chunk size consideration

The current code reads 160 bytes (with stop_event) or 640 bytes (without)
from ffmpeg stdout. With native mulaw, ElevenLabs returns chunks in its own
sizes. Two options:

**Option A:** Stream chunks as-is from the API. `put_outbound_sync` and
the transport handle arbitrary chunk sizes. Simpler, fewer copies.

**Option B:** Buffer and emit 160-byte frames. Matches the existing frame
size contract (FRAME_BYTES=160, 20ms). More predictable timing.

Recommend **Option A** — the transport already handles arbitrary chunk sizes
(base64-encodes and sends to Twilio). The 160-byte framing was an artifact
of reading from ffmpeg stdout, not a protocol requirement.

### Test updates

`test_elevenlabs_tts.py` currently mocks `subprocess.Popen` and ffmpeg
pipes. After this change:

- Remove all `subprocess.Popen` mocking
- Mock `client.text_to_speech.convert()` to return an iterator of mulaw bytes
- Barge-in test: set stop_event before iteration, verify early return
- Mark timeout test: unchanged (tests send_mark_and_wait behavior)

3 of 6 tests need mock updates. Test count stays the same.

## Acceptance Criteria

- [ ] `output_format="ulaw_8000"` in ElevenLabsTTS.speak()
- [ ] No `subprocess` import in elevenlabs_tts.py
- [ ] No ffmpeg process spawned during TTS
- [ ] All 6 voice_runtime TTS tests pass (mock updates)
- [ ] All outcaller tests pass (211)
- [ ] All ninchat_voice tests pass (361)
- [ ] Verified with live ElevenLabs call: audio plays correctly on phone

## Effort

**Estimated: 0.25 day**

One parameter change, ~50 lines deleted, 3 test mock updates. The live
verification is the longest part.

## Not in scope

- Changing STT (Scribe already uses mulaw natively)
- Alternative providers (GCP, Azure) — separate work
- Removing ffmpeg from Dockerfile (may still be needed for predefined audio
  generation script; verify before removing)
