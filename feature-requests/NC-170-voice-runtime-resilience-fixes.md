# NC-170: voice_runtime Resilience Fixes

**Status:** Approved
**Date:** 2026-03-20
**Ref:** voice_runtime code review (2026-03-20),
         NC-155 (test gaps — related but separate scope)

## Judgement

**Verdict: APPROVED — well-scoped, verified against source. Amendments below.**

All 5 fixes verified against the actual source code. The FR correctly
identifies real issues and proposes proportionate solutions. Code
references are accurate with one correction noted below.

**Verified claims:**
1. `session.py:149` — confirmed `except Exception: pass` with no logging.
   The `clear_inbound()` drain is best-effort but should at minimum log
   at DEBUG for production tracing.
2. `elevenlabs_stt.py:128-147` — confirmed `_reconnect_after_error()`
   calls `await self._connect()` immediately with no delay. The method is
   called from `_on_error` via `run_coroutine_threadsafe` on fatal errors
   (`queue_overflow`, `resource_exhausted`). Immediate retry is genuinely
   problematic.
3. `session.py:202-205` — confirmed `_disconnect_requested` and
   `_clear_queue` initialized in `signal_ws_connected()`. The
   `request_disconnect()` method at line 283 correctly guards with
   `if self._disconnect_requested is None: return` but logs only at
   DEBUG — a pre-connection disconnect request is silently swallowed
   from the caller's perspective.
4. `session.py:113` `put_inbound()` — confirmed no size validation.
   However: Twilio actually sends **variable-length** payloads. The base64
   decode in `twilio_ws.py:149` produces whatever Twilio sends. In practice
   it's 160 bytes but the Twilio docs don't guarantee this. The FR's
   log-only approach is correct.
5. `audio.py:193-206` `shutdown()` — confirmed `proc.wait(timeout=2.0)`
   is inside `contextlib.suppress(Exception)`, meaning a TimeoutExpired
   would be silently swallowed and ffplay left running. The FR's fix is
   correct — catch TimeoutExpired explicitly, then kill.

**Amendments:**

1. **Fix 1 (stt_tee.py:94) — drop counter needs `__init__` setup.**
   The FR proposes `self._secondary_drops += 1` but doesn't show where
   `_secondary_drops` is initialized. Add `self._secondary_drops: int = 0`
   to `SttTee.__init__()`. Also reset it in `stop()` for multi-call reuse.

2. **Fix 2 (backoff) — `import random` should be at module top, not inside
   method.** The FR shows `import random` inside `_reconnect_after_error()`.
   Move to module-level imports. Also: `_reconnect_attempt` must be
   initialized in `__init__` (line 38-49), not as a class variable — each
   instance needs its own counter.

3. **Fix 3 (event race) — also keep the init in `signal_ws_connected()`
   as a safety net.** The FR says "remove the same initialization from
   signal_ws_connected()". Don't remove it — keep it as a defensive
   fallback. `set_loop()` might not be called in all code paths (e.g.,
   tests that mock the transport). The `if ... is None` guard already
   makes double-init safe. Cost: zero. Benefit: defense-in-depth.

4. **Fix 4 (frame size) — Twilio sends variable chunk sizes.** The
   `twilio_ws.py:149` base64 decode produces whatever Twilio sends per
   WebSocket message. Twilio's default is 160 bytes (20ms at 8kHz mulaw)
   but this is not contractual. The log message should say "non-standard"
   not "unexpected" — variable sizes are valid, just uncommon. Also:
   only log the **first** occurrence per session to avoid log spam if
   Twilio changes behavior.

5. **Fix 5 (sigkill) — the existing code wraps `proc.wait(timeout=2.0)`
   in `contextlib.suppress(Exception)` (line 204).** The FR's proposed
   code replaces the suppress with an explicit try/except, which is
   correct. But note that the suppress must be removed, not just the
   kill added after it. The FR's code block shows this correctly but
   doesn't call it out explicitly.

6. **Bonus (string matching) — `ConnectionError` may not be the right
   exception.** The Starlette/FastAPI WebSocket raises
   `starlette.websockets.WebSocketDisconnect` (already caught at line 163)
   and `RuntimeError("Unexpected ASGI message type")` for closed
   connections. The string check at line 167 likely catches RuntimeError
   with "not connected" message. Replace with:
   ```python
   except RuntimeError as e:
       if "not connected" in str(e).lower() or "unexpected asgi" in str(e).lower():
           logger.info("WebSocket closed (server-initiated)")
       else:
           logger.error("WebSocket runtime error: %s", e)
       session.signal_disconnected()
   except Exception as e:
       logger.error("WebSocket error: %s", e)
       session.signal_disconnected()
   ```
   This is more precise than the FR's `ConnectionError` suggestion.

**Authority granted.** All fixes are low-risk, well-scoped, and
independently testable. Execute in the order listed (Fix 1-5, then Bonus).
Each fix can be a separate commit for clean git history.

## Problem

A code review of voice_runtime identified 5 resilience issues that
individually are minor but collectively reduce production observability
and fault tolerance:

1. **Silent exception handlers** — three locations swallow errors without
   logging, making production debugging harder
2. **STT reconnect has no backoff** — immediate retry can hammer the
   ElevenLabs API during transient outages
3. **Lazy asyncio.Event race** — transport intent fields created on
   WebSocket connect, not session init; early calls silently dropped
4. **No audio frame validation** — `put_inbound()` accepts any byte
   length; oversized frames break AudioMixer assumptions
5. **AudioMixer zombie risk** — ffplay process not killed after timeout

None of these are currently causing known production failures, but all
are time bombs for high-load or degraded-network conditions.

## Fixes

### Fix 1: Add logging to silent exception handlers

**3 locations, ~6 lines changed.**

**`session.py:149`** — `clear_inbound()` best-effort drain:
```python
# Before
except Exception:
    pass  # best-effort

# After
except Exception:
    logger.debug("clear_inbound drain failed", exc_info=True)
```

**`stt_tee.py:94-95`** — secondary queue overflow in `_fanout()`:
```python
# Before
except Exception:
    pass  # secondary queue overflow — don't block primary

# After
except Exception:
    pass  # secondary queue overflow — don't block primary
    # Note: intentionally silent — logging every dropped frame at 50fps
    # would be noisy. The secondary STT is best-effort by design.
    # Consider a counter + periodic log if this becomes a concern.
```

Keep `stt_tee.py:94` as-is (intentional — 50fps logging is noisy). Add
a frame drop counter that logs periodically instead:

```python
# In _fanout():
self._secondary_drops += 1
if self._secondary_drops % 500 == 1:
    logger.warning("Secondary STT queue overflow: %d frames dropped", self._secondary_drops)
```

**`stt_tee.py:62`** — `set_speaking()` secondary failure:
Already has `exc_info=True` logging. No change needed (the review
flagged this incorrectly — the code at line 63 already logs with
`logger.warning("Secondary STT set_speaking failed", exc_info=True)`).

### Fix 2: Exponential backoff for STT reconnect

**`elevenlabs_stt.py:128-147`** — `_reconnect_after_error()`

Currently reconnects immediately. Add exponential backoff with jitter:

```python
_RECONNECT_BASE_DELAY_S = 1.0
_RECONNECT_MAX_DELAY_S = 30.0
_reconnect_attempt: int = 0

async def _reconnect_after_error(self) -> None:
    """Reconnect Scribe after a fatal error with exponential backoff."""
    # Drain stale frames (existing logic, unchanged)
    if self._inbound_queue:
        ...

    delay = min(
        self._RECONNECT_BASE_DELAY_S * (2 ** self._reconnect_attempt),
        self._RECONNECT_MAX_DELAY_S,
    )
    # Jitter: ±25% to prevent thundering herd
    import random
    delay *= 0.75 + random.random() * 0.5

    logger.info("Reconnecting Scribe in %.1fs (attempt %d)...", delay, self._reconnect_attempt + 1)
    await asyncio.sleep(delay)

    try:
        await self._connect()
        logger.info("Scribe reconnected successfully (attempt %d)", self._reconnect_attempt + 1)
        self._reconnect_attempt = 0  # Reset on success
    except Exception as exc:
        self._reconnect_attempt += 1
        logger.error("Scribe reconnect failed (attempt %d): %s", self._reconnect_attempt, exc)
```

Reset `_reconnect_attempt = 0` in `_connect()` success path and on
normal `set_speaking(False)` reconnect (which is proactive, not error-driven).

### Fix 3: Fix lazy asyncio.Event initialization race

**`session.py:202-205`** — `signal_ws_connected()`

The `_disconnect_requested` and `_clear_queue` fields are `None` until
`signal_ws_connected()` runs. If consumer code calls
`request_disconnect()` or `request_clear_buffer()` before WebSocket
connection, the request is silently lost.

Move initialization to `set_loop()` instead — this is called from the
async context before any consumer interaction, but before WebSocket
connection:

```python
def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
    """Set the event loop for cross-thread scheduling."""
    self._loop = loop
    # NC-170: initialize transport intent fields in loop context
    # (moved from signal_ws_connected to close the pre-connection race)
    if self._disconnect_requested is None:
        self._disconnect_requested = asyncio.Event()
    if self._clear_queue is None:
        self._clear_queue = asyncio.Queue()
```

Remove the same initialization from `signal_ws_connected()` (lines 202-205).

**Alternative considered:** Initialize in `__post_init__` — rejected because
`asyncio.Event()` and `asyncio.Queue()` need to be created in the right
event loop context. `set_loop()` is the earliest safe point.

### Fix 4: Audio frame size validation

**`session.py:113`** — `put_inbound()`

Add validation to catch malformed frames early:

```python
# Expected frame size: 160 bytes = 20ms at 8kHz mulaw mono
FRAME_BYTES = 160

def put_inbound(self, data: bytes) -> None:
    """Enqueue inbound audio frame (sync, called from transport)."""
    if len(data) != FRAME_BYTES:
        # Twilio sends 160-byte frames. Log unexpected sizes but don't drop.
        if len(data) > 0:
            logger.debug("put_inbound: unexpected frame size %d (expected %d)", len(data), FRAME_BYTES)
    ...
```

Log-only, not reject — Twilio documentation says 160 bytes but we
shouldn't drop frames from transport changes we haven't verified.
The AudioMixer can handle variable sizes; this is observability, not
enforcement.

### Fix 5: AudioMixer sigkill fallback

**`audio.py:193-206`** — `shutdown()`

After `proc.wait(timeout=2.0)`, if the process is still alive, send
SIGKILL:

```python
def shutdown(self) -> None:
    """Stop mix thread and ffplay process."""
    self._running = False
    if self._thread:
        self._thread.join(timeout=2.0)
    if self._proc:
        pid = self._proc.pid
        with contextlib.suppress(OSError):
            if self._proc.stdin:
                self._proc.stdin.close()
        self._proc.terminate()
        try:
            self._proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            logger.warning("AudioMixer: ffplay pid=%d did not terminate, sending SIGKILL", pid)
            self._proc.kill()
            self._proc.wait(timeout=1.0)
        logger.info("AudioMixer shutdown (pid=%d)", pid)
```

Requires `import subprocess` (already imported for `Popen`).

## Bonus: String-based error matching

**`twilio_ws.py:167`** — `if "not connected" in str(e).lower()`

Low priority but fragile. Improve by catching the specific exception:

```python
except WebSocketDisconnect:
    logger.info("WebSocket disconnected")
    session.signal_disconnected()
except ConnectionError as e:
    logger.info("WebSocket closed: %s", e)
    session.signal_disconnected()
except Exception as e:
    logger.error("WebSocket error: %s", e)
    session.signal_disconnected()
```

This is a minor improvement — only include if touching the file anyway.

## Test Plan

| Fix | Test approach |
|-----|---------------|
| Fix 1: Logging | Verify log output with `caplog` fixture. New test: `test_clear_inbound_logs_on_failure` |
| Fix 2: Backoff | Mock `asyncio.sleep`, verify delays increase: 1s → 2s → 4s → ... → 30s. Verify reset on success |
| Fix 3: Event race | Call `request_disconnect()` after `set_loop()` but before `signal_ws_connected()`. Verify the event is set |
| Fix 4: Frame size | Call `put_inbound(b"\x00" * 320)` and verify debug log emitted. Verify frame still enqueued |
| Fix 5: Sigkill | Mock `proc.wait()` to raise `TimeoutExpired`, verify `proc.kill()` called |
| Bonus: Exception | Mock WebSocket to raise `ConnectionError`, verify `signal_disconnected()` called |

## Acceptance Criteria

- [ ] No `except Exception: pass` without logging or documented rationale
- [ ] `_reconnect_after_error()` delays with exponential backoff (1s → 30s cap)
- [ ] `_reconnect_attempt` resets to 0 on successful connect
- [ ] `request_disconnect()` works after `set_loop()` but before WS connect
- [ ] Unexpected frame sizes logged at DEBUG level
- [ ] AudioMixer sends SIGKILL after 2s terminate timeout
- [ ] All existing voice_runtime tests pass (81 tests)
- [ ] New tests for each fix (6+ new tests)

## Effort Estimate

| Fix | Effort | Notes |
|-----|--------|-------|
| Fix 1: Silent exceptions | 15 min | Add logging + drop counter |
| Fix 2: Backoff | 30 min | Implementation + test |
| Fix 3: Event race | 20 min | Move init + test race scenario |
| Fix 4: Frame validation | 15 min | Log-only + test |
| Fix 5: Sigkill fallback | 15 min | Already has terminate; add kill path |
| Bonus: Exception types | 10 min | Only if touching twilio_ws.py |
| **Total** | **~2 hours** | |

## What NOT to Do

- **Do not add frame rejection** — log unexpected sizes, don't drop frames.
  We haven't verified all Twilio edge cases (start-of-stream, reconnect).
- **Do not add TTS Protocol** — NC-169 was rejected. TTS providers don't
  live on VoiceSession; no structural interface needed yet.
- **Do not add retry limits to backoff** — let the backoff cap at 30s and
  keep retrying. The session will eventually disconnect/timeout via other
  mechanisms (FSM timeout, Twilio hangup).
- **Do not change queue drain to be atomic** — the race in `clear_inbound()`
  is theoretical. Adding locks would complicate the async/sync bridge for
  minimal benefit. Revisit if barge-in issues are reported.
