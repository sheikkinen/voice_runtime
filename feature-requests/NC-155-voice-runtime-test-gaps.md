# NC-155: Voice Runtime Test Gaps (TDD Process Correction)

**Status:** Approved
**Date:** 2026-03-19
**Ref:** `docs/voice-review.md` (Section 3, P5–P7)

## Judgement

**Verdict: APPROVED — clear, minimal, correctly scoped.**

The FR correctly identifies a TDD process failure: code was extracted from
consumers with integration-level coverage, masking the missing unit tests.
The "Lesson" paragraph is the right takeaway.

**Strengths:**
- Each test is named, with a one-line validation statement — concrete, auditable
- Effort estimate (0.5 day = 14 tests, ~260 lines) is realistic
- Root cause analysis is honest: "RED phase should have caught these"
- Scope is bounded — no scope creep into integration tests

**Amendments:**
1. The `DEFAULT_BRIDGE_PATH` count says 6 in voice-review.md but actual count
   is 7 (bridge_listener.py also defines it). NC-156 should account for this.
   Not an NC-155 concern but noted for cross-reference.
2. The `test_listen_api_error_returns_empty` test should verify the *error is
   logged*, not just that it returns empty. Silent failure is a P6 violation.
3. Consider adding `test_listen_barge_in_event_stops_early` for PerTurnStt —
   the outcaller uses `stop_event` with TTS but the STT barge-in path via
   `session.is_disconnected` is the only interrupt mechanism. This is optional.

**Authority granted.** Write RED tests first. 14 tests → 95+ total.

## Process Issue

NC-152 Phase 2a extracted voice_runtime using TDD — 81 tests written RED
before implementation. However, **two modules shipped with zero unit tests:**

1. `PerTurnStt.listen()` — the STT mode outcaller uses in production
2. `transports/twilio_call.py` — Twilio REST call initiation + TwiML generation

Additionally, the transport handler's STT lifecycle (`stt_factory` creation,
`stt.start()`, `stt.stop()`) has no test coverage despite being the
integration point that ninchat_voice depends on.

This is a TDD process failure. The RED phase should have caught these gaps —
code without a failing test should not have been written. The root cause:
these modules were extracted from consumers that already had integration-level
coverage, so the missing unit tests weren't noticed during the GREEN phase.

**Lesson:** When extracting shared code via TDD, audit every public method
for a corresponding unit test, not just every module.

## Scope

### voice_runtime/providers/elevenlabs_stt.py — PerTurnStt

Currently untested in voice_runtime. Tested indirectly via
outcaller/tests/unit/test_telco_nodes.py (which mocks at a higher level).

**Tests to add:**

| Test | What it validates |
|------|-------------------|
| `test_listen_returns_transcript` | Happy path: feed audio, get committed transcript |
| `test_listen_timeout_returns_empty` | Timeout: no speech → returns "" |
| `test_listen_disconnect_returns_empty` | session.is_disconnected mid-listen → returns "" |
| `test_listen_no_loop_returns_empty` | session.loop is None → returns "" immediately |
| `test_listen_closes_connection` | Scribe connection closed in finally block |
| `test_listen_api_error_returns_empty` | ElevenLabs connect raises → returns "" |

**Estimated: 6 tests, ~120 lines.**

### voice_runtime/transports/twilio_ws.py — STT lifecycle

The handler creates STT via `stt_factory()`, calls `stt.start(inbound)` on
stream start, and `stt.stop()` on disconnect. None of this is tested.

**Tests to add:**

| Test | What it validates |
|------|-------------------|
| `test_stt_factory_called_on_start` | `stt_factory()` called once on "start" event |
| `test_stt_start_awaited` | `stt.start(session.inbound)` awaited after factory |
| `test_stt_stop_on_disconnect` | `stt.stop()` awaited in finally block |
| `test_no_stt_when_factory_none` | No STT created when `stt_factory` is None |

**Estimated: 4 tests, ~80 lines.**

### voice_runtime/transports/twilio_call.py

`initiate_outbound_call()` and `build_stream_twiml()` have zero tests.

**Tests to add:**

| Test | What it validates |
|------|-------------------|
| `test_build_stream_twiml_structure` | Valid TwiML XML with Stream element |
| `test_build_stream_twiml_wss_conversion` | https:// → wss:// in stream URL |
| `test_initiate_missing_env_raises` | MissingStreamUrlError when VOICE_STREAM_URL unset |
| `test_build_stream_xml_alias` | `build_stream_xml` is alias for `build_stream_twiml` |

**Estimated: 4 tests, ~60 lines.**

## Acceptance Criteria

- [ ] 6 PerTurnStt unit tests passing in voice_runtime
- [ ] 4 STT lifecycle tests passing in voice_runtime
- [ ] 4 twilio_call tests passing in voice_runtime
- [ ] voice_runtime test count: 81 → 95+
- [ ] All existing consumer tests still pass

## Effort

**Estimated: 0.5 day**

All tests mock external dependencies (ElevenLabs SDK, Twilio SDK). No
integration tests needed — those exist in consumers.
