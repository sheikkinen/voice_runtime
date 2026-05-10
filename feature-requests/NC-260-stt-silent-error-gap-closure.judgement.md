# NC-260 Judgement: STT Silent Error Gap Closure

**Verdict:** APPROVED with amendments
**Judge:** Copilot
**Date:** 2026-04-28

## Assessment

The FR correctly identifies six gaps sharing the NC-258 silent-death pattern. All claims verified against code. The scope is well-bounded and the priority order is sound. However, the FR has structural issues that must be resolved before enforcement.

## Amendments

### J-1: Title is misleading — rename to "Silent Error Gap Closure"

The title says "STT" but 3 of 6 items (A, B, F) are not STT-related. Gap A is TTS, Gap B is WebSocket transport, Gap F is session lifecycle. The audit doc uses "Silent Error Gaps" — the FR should match.

**Action:** Rename to `NC-260: Silent Error Gap Closure`.

### J-2: Gap D is incomplete — 5 coordinators, not 4

The FR lists 4 coordinator configs missing `stt_error`. The audit missed `voice_coordinator_triage.yaml` which also has zero `stt_error` transitions.

**Action:** Add `voice_coordinator_triage.yaml` to Gap D scope.

### J-3: Gap A needs a `TtsProvider` protocol — state this explicitly

NC-258 added `on_error` to the existing `SttProvider` protocol. For TTS, no protocol exists at all. The design says "Add `on_error` to TTS provider protocol" but doesn't mention creating the protocol. This is not a minor detail — it's a prerequisite.

**Action:** Add explicit step: "Create `TtsProvider` Protocol in `providers/__init__.py` mirroring `SttProvider`." This defines the contract before adding `on_error`.

### J-4: Gap E design is over-engineered — simplify

The proposed `session.stt_callbacks` dict + `setattr` loop in `twilio_ws.py` is unnecessarily generic. The callbacks are a known, fixed set (`on_committed`, `on_recognizing`, `on_error`). A generic `stt_callbacks` dict invites arbitrary future attributes.

**Simpler approach:** Wire callbacks in `_on_speak()` as today, but add an `_ensure_stt_callbacks()` helper called from `create_bridge_handlers()` that registers a one-shot `session.on_stt_ready` callback. When `twilio_ws.py` creates `session.stt`, it calls `session.on_stt_ready(session.stt)` which wires the three callbacks immediately. This keeps the cross-layer coupling explicit and typed.

Alternative (even simpler): keep wiring in `_on_speak()` but also wire in `_on_listen()` which fires on FSM entering `listening` — this is earlier than `_on_speak()` and already exists. Verify whether `_on_listen()` fires before `_on_speak()` in the navigator flow.

**Action:** Replace generic `stt_callbacks` dict with one of the two simpler approaches. Investigate `_on_listen()` timing first.

### J-5: Gap F — choose one approach, not two

The FR offers both "buffer frames" and "raise RuntimeError" as alternatives. Pick one. The buffer approach adds complexity (drain logic, cap management, memory concern). The `RuntimeError` approach is simpler but may crash the transport.

**Recommendation:** Log a warning + increment a counter on first occurrence. Don't buffer, don't raise. The `_loop` race is microsecond-scale; if it happens persistently, the session is broken anyway. A counter lets monitoring detect it. This is the lowest-priority gap for a reason — don't over-engineer it.

**Action:** Design as "log warning on `_loop is None`, no silent return." Remove buffer alternative.

### J-6: Gap C — verify error types against ElevenLabs API docs

The FR proposes 7 error types in `_FATAL_ERRORS` but doesn't cite the ElevenLabs WebSocket API documentation. Some of these (`auth_error`, `input_error`) may use different message types. The existing 2 types (`queue_overflow`, `resource_exhausted`) are verifiable from the SDK.

**Action:** Before enforcement, check the ElevenLabs STT WebSocket API docs for the canonical list of error message types. Don't guess.

### J-7: Separate commits per gap — enforce atomicity

Each gap (A–F) must be a separate commit. They touch different files, have independent tests, and can be deployed independently. A single monolithic commit would violate `mixed_commits_erode_auditability`.

**Action:** Enforce one commit per gap, in priority order (B → A → D → C → E → F). Each commit must include its test.

## Scope Confirmation

The following are correctly **out of scope**:
- AudioMixer ffplay death (#7) — monitoring concern, not call-critical
- SttTee secondary swallowing (#8) — by-design isolation
- Mark sync timeout (#9) — cascading timeout, not silent death

The following is **missing and should be noted**:
- Task leak in `call_cleanup` (the ghost `tasks=12` evidence). This is related but is a separate cleanup concern, not a silent error gap. Acknowledge in FR that the task leak is a separate issue.

## Risk Assessment

- **Gap B** (Twilio WS): Near-zero risk. Adding `signal_disconnected()` to existing exception handlers. Worst case: extra disconnect signal on transient error (but disconnect is idempotent).
- **Gap A** (TTS `on_error`): Medium risk. Needs the new `TtsProvider` protocol. Reconnect logic for Azure Speech SDK synthesis is less documented than recognizer reconnect.
- **Gap D** (FSM parity): Near-zero risk. YAML-only change. Test with scenario FSM tests.
- **Gap C** (ElevenLabs STT): Low risk if error types are verified against docs (J-6).
- **Gap E** (callback wiring): Medium risk. Cross-layer timing change. Must not break existing callback semantics.
- **Gap F** (loop=None): Low risk with the simplified J-5 approach.

## Final

Freeze scope after amendments. Six gaps, six commits, priority order B → A → D → C → E → F.
Authority granted for enforcement.
