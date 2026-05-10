# voice_runtime 0.1.0 — Porting Guide

This document describes **breaking changes** introduced during the 0.1.0 pre-publish
cleanup and what consuming projects must update before upgrading.

---

## Quick summary

| Area | Old behaviour | New behaviour |
|------|--------------|---------------|
| STT language code | Defaulted silently to `"fi"` (Finnish) | Defaults to `"en"`; set `STT_LANGUAGE_CODE` env var or pass `language_code=` explicitly |
| Stream URL | Fell back to `NGROK_URL` if `VOICE_STREAM_URL` unset | `VOICE_STREAM_URL` is the only source; missing raises `RuntimeError` |
| Transport factory | `create_transport()` returned a Python module | Removed; import transport helpers directly |
| Package install | Internal path dependency | `pip install voice-runtime` (PyPI) or `pip install -e path/to/voice_runtime` |
| ffplay | Silent `BrokenPipeError` if missing | `AudioMixer.start()` raises `RuntimeError` with install instructions |
| MockTts marks | Always called `send_mark_and_wait`, required live loop | Skips mark when session has no event loop (safe in unit tests) |

---

## 1 — STT language code

**What changed:** `ElevenLabsStt` previously read `STT_LANGUAGE_CODE` from the
environment and defaulted to `"fi"` (Finnish) when the variable was absent.
The default is now `"en"`.

**Who is affected:** Any deployment that relied on the implicit Finnish default
without setting `STT_LANGUAGE_CODE` explicitly — i.e. Finnish-language callers
on a server where the env var was never set.

**Action required:**

```bash
# In your .env or deployment environment
STT_LANGUAGE_CODE=fi
```

Or pass it at construction time:

```python
from voice_runtime.stt import create_stt

stt = create_stt(provider="elevenlabs", language_code="fi")
```

---

## 2 — Stream URL: VOICE_STREAM_URL replaces NGROK_URL

**What changed:** `initiate_outbound_call()` previously fell back to `NGROK_URL`
when `VOICE_STREAM_URL` was not set. The `NGROK_URL` fallback has been removed.
`VOICE_STREAM_URL` is now the only configuration point.

**Who is affected:** Any consumer that set `NGROK_URL` instead of (or in addition
to) `VOICE_STREAM_URL` in local development or staging environments.

**Action required:**

```bash
# Remove:
# NGROK_URL=https://abc123.ngrok-free.app

# Add / rename:
VOICE_STREAM_URL=https://abc123.ngrok-free.app/voice/ws
```

The value must be a **public WebSocket URL** reachable by Twilio. In production
this is your load-balancer or reverse-proxy endpoint. In local dev, use ngrok,
cloudflared, or similar — but always via `VOICE_STREAM_URL`.

---

## 3 — Transport factory removed

**What changed:** `create_transport()` returned a Python **module object**, not a
typed transport instance, and had no known callers in production code. It has been
removed.

**Who is affected:** Code that explicitly called `create_transport()`. Both known
consumers (`ninchat_voice` and `outcaller`) already used direct imports before
this change and are **unaffected**.

**Action required** (only if you called `create_transport()` directly):

```python
# Before
from voice_runtime.transport import create_transport
transport = create_transport()
ws = transport.register_voice_websocket  # module attribute access

# After — import what you need directly:
from voice_runtime.transports.twilio_ws import register_voice_websocket
from voice_runtime.transports.twilio_call import initiate_outbound_call, build_stream_twiml

# SMS delivery (unchanged API, renamed function):
from voice_runtime.transport import get_sms_transport
sms = get_sms_transport()
```

**Note on extensibility:** `create_transport()` was the intended extension seam
for future non-Twilio transports. Its removal means adding a second transport
provider will require a new factory pattern. File an issue before adding a second
transport backend.

---

## 4 — Package installation

**What changed:** `voice_runtime` is now a proper Python package with a
`pyproject.toml`. Optional provider dependencies are behind extras.

**Action required:**

```bash
# Core (Twilio + FastAPI only)
pip install voice-runtime

# With ElevenLabs STT/TTS
pip install "voice-runtime[elevenlabs]"

# With Azure Speech
pip install "voice-runtime[azure]"

# Development (adds pytest, pytest-asyncio, pytest-mock)
pip install "voice-runtime[dev]"
```

If you previously added `voice_runtime` to your project via a relative path in
`sys.path` manipulation or `requirements.txt` file path, switch to the package
install above.

---

## 5 — AudioMixer requires ffplay

**What changed:** `AudioMixer.start()` now checks for `ffplay` on `PATH` at
startup and raises `RuntimeError` with clear instructions if it is missing.
Previously the mixer started silently and died with `BrokenPipeError` when
the first audio chunk arrived.

**Who is affected:** Any server that runs `AudioMixer` without `ffmpeg` installed.

**Action required:**

```bash
# macOS
brew install ffmpeg

# Ubuntu / Debian
apt-get install -y ffmpeg

# Alpine
apk add ffmpeg
```

---

## 6 — MockTts in unit tests

**What changed:** `MockTts.speak()` now skips `send_mark_and_wait` when
`session._loop` is `None`. Previously it unconditionally called mark sync,
which required a live asyncio event loop even in pure unit tests.

**Who is affected:** Tests that constructed `MockTts` without a full
`VoiceSession` wiring.

**No migration needed** — the new behaviour is strictly more permissive for
test code. If you have tests that asserted on the mark call being fired without
a live loop, those assertions will now need a wired session.

---

## 7 — Public API surface

`voice_runtime.__init__` now exports a stable public API. Prefer importing
from the top-level package rather than internal modules:

```python
# Preferred
from voice_runtime import VoiceSession, create_stt, create_tts, AudioMixer

# Still works, but internal (may change)
from voice_runtime.session import VoiceSession
from voice_runtime.stt import create_stt
```

---

## Environment variable reference

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `VOICE_STREAM_URL` | Yes (for outbound calls) | — | Public WebSocket URL for Twilio stream |
| `TWILIO_ACCOUNT_SID` | Yes (for outbound calls) | — | |
| `TWILIO_AUTH_TOKEN` | Yes (for outbound calls) | — | |
| `TWILIO_FROM_NUMBER` | Yes (for outbound calls) | — | E.164 format |
| `ELEVENLABS_API_KEY` | Yes (ElevenLabs provider) | — | |
| `STT_LANGUAGE_CODE` | No | `en` | BCP-47 language code for STT |
| `STT_MODEL` | No | `scribe_v1` | ElevenLabs STT model |
| `TTS_VOICE_ID` | No | `21m00Tcm4TlvDq8ikWAM` | ElevenLabs voice ID |
| `AZURE_SPEECH_KEY` | Yes (Azure provider) | — | |
| `AZURE_SPEECH_REGION` | No | `westeurope` | Azure region |

See `.env.example` for a complete template.
