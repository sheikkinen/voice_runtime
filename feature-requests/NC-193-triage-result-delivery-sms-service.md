# NC-193: Triage-First Result Delivery (Graph Node + SMS Service)

**Priority:** HIGH
**Type:** Feature
**Status:** Judged — Approved with amendments (v2)
**Effort:** 3 days
**Requested:** 2026-04-01
**Judged:** 2026-04-01 (v2)

## Summary

Implement result delivery for the first production target, `medical_triage`, using a graph-level `send_results` node and a runtime-owned SMS transport. Delivery runs inside the questionnaire graph after completion and before farewell, so each graph can own its own delivery policy while Twilio ownership stays in `voice_runtime`.

Phase 1 scope is SMS delivery only (triage-first). Ninchat callback forwarding is designed into the same delivery node contract and added in Phase 2.

## Value Statement

Today callers complete triage but receive no structured confirmation. This feature closes that gap with immediate post-completion delivery and establishes a reusable graph-level pattern for all future questionnaires.

## Problem

Current `ninchat_voice` state:
- `graphs/medical_triage/graph.yaml` completes without any delivery step.
- No `send_results` tool node exists in triage graph.
- No questionnaire-level delivery orchestrator exists in `projects/ninchat_voice`.
- No Twilio SMS transport exists yet in `projects/voice_runtime`.
- Coordinator already has caller number (`incoming_call.caller`), but it is not used for result delivery.

Impact:
- Caller gets verbal farewell only; no persistent result summary.
- No evented result handoff path exists for downstream services.

## Decision

Adopt **graph-level delivery** for the first target:
- Add a Python tool node `send_results` to `medical_triage` graph.
- Node executes after completion signal and before final farewell response.
- Node calls a questionnaire delivery orchestrator which delegates SMS send to `voice_runtime` transport.

Why this choice:
- Keeps questionnaire behavior self-contained.
- Matches questionnaire-api delivery pattern while fitting current navigator/FSM routing.
- Preserves existing runtime boundary: telephony and Twilio SDK usage stay in `voice_runtime`.
- Avoids coordinator-specific branching for each questionnaire.

## Scope

In scope (Phase 1):
- `medical_triage` graph wiring for delivery node.
- Result delivery orchestrator and formatter for triage payload in `ninchat_voice`.
- Twilio SMS transport implementation in `voice_runtime`.
- Context propagation of caller number to graph state (`sms_to`).
- Delivery status recorded in graph state for observability/tests.

Out of scope (Phase 1):
- interRAI-CA delivery wiring.
- Ninchat queue callback HTTP delivery.
- Phone number collection prompts (use Twilio caller number from FSM).

## Design

### D1. Graph Changes (`graphs/medical_triage/graph.yaml`)

Add new state keys:
- `sms_to: str`
- `delivery_result: dict`
- `phase: str` updated to `done` on completion

Add tool:
- `send_results` -> `module: services.result_delivery`, `function: send_results`

Add node:
- `send_results` (`type: python`, `tool: send_results`, `state_key: delivery_result`)

Edge order:
1. `mark_complete`
2. `send_results`
3. `generate_farewell`
4. `END`

### D2. Triage Python Node Helpers (`graphs/medical_triage/medical_triage.py`)

- Update `mark_complete()` to return `{"complete": True, "phase": "done"}`.
- Add helper (or passthrough node) to capture SMS target from state, expecting `sms_to` in graph state.

### D3. Coordinator Context Bridge (`config/voice_coordinator_navigator.yaml`)

In `graph_processing -> yamlgraph_async -> variables`, pass caller to graph:
- `sms_to: "{caller}"`

This uses existing `incoming_call.context_map.caller` and avoids extra voice collection steps.

### D4. Delivery Service (`services/result_delivery.py`)

Create `send_results(state: dict) -> dict`:
- Input:
  - `sms_to`
  - `extracted`
  - `recap_summary`
  - `phase`
  - optional `result`
- Behavior:
  - If `sms_to` missing/invalid -> skip with explicit status.
  - Build SMS text via formatter.
  - Send via runtime transport (`voice_runtime.transports.twilio_sms.send_sms`).
  - Return `delivery_result` with `status`, `channel`, and IDs.
- Error policy:
  - Never crash graph for delivery failure.
  - Return structured failure payload (`status: failed`, `error: ...`) and continue to farewell.

### D5. Runtime SMS Transport (`projects/voice_runtime/transports/twilio_sms.py`)

Create Twilio-backed SMS transport in runtime (same ownership as `twilio_call.py` and `twilio_ws.py`):
- Reads env vars:
  - `TWILIO_ACCOUNT_SID`
  - `TWILIO_AUTH_TOKEN`
  - `TWILIO_PHONE_NUMBER`
- Public API (runtime):
  - `send_sms(to: str, body: str) -> dict`
- Contract:
  - Runtime owns Twilio SDK import and provider-specific mapping.
  - Callers receive normalized metadata (`message_sid`, `status`, `to`).

### D6. Runtime Factory (`projects/voice_runtime/transport.py`)

Extend runtime transport factory for messaging use:
- Add a transport accessor for SMS provider dispatch (initially `twilio`).
- Keep provider checks in runtime to avoid Twilio branching in `ninchat_voice`.

### D7. SMS Formatter (`services/sms_formatter.py`)

Create triage formatter:
- Include concise Finnish summary using:
  - `chief_complaint`
  - `duration`
  - `recent_changes`
  - `recap_summary` fallback
- Keep message size bounded for SMS constraints.

## Acceptance Criteria

1. Completing `medical_triage` triggers one delivery attempt before farewell.
2. Graph output includes `delivery_result` with `status` (`sent|skipped|failed`).
3. `mark_complete` sets `phase: done`.
4. Missing `sms_to` does not fail graph; delivery marked `skipped`.
5. Runtime send success stores provider message ID in `delivery_result`.
6. Existing triage conversation behavior (opening/probing/recap loop) remains unchanged.

## Test Plan (TDD)

### Unit

- `projects/voice_runtime/tests/test_twilio_sms.py`
  - env missing -> explicit initialization failure
  - successful send returns normalized metadata
- `tests/unit/test_sms_formatter.py`
  - triage payload formatting
  - recap fallback formatting
  - max-length clipping behavior
- `tests/unit/test_result_delivery.py`
  - skip when `sms_to` missing
  - sent when runtime transport returns success
  - failed when runtime transport raises exception

### Graph/Integration

- `tests/test_nc193_triage_graph_delivery_wiring.py`
  - asserts `send_results` node exists and sits between `mark_complete` and `generate_farewell`
- `tests/test_nc193_navigator_passes_sms_to.py`
  - asserts navigator coordinator passes `sms_to` in `yamlgraph_async.variables`
- `tests/integration/test_nc193_triage_delivery_flow.py`
  - run triage completion with mocked runtime SMS transport
  - assert `delivery_result.status == sent`
  - assert final event remains `graph_done`

## Rollout Plan

1. Implement NC-193 for `medical_triage` only.
2. Observe logs and message content in staging.
3. Extend same node/service pattern to `interrai_ca` (follow-up NC).
4. Add Ninchat callback channel into `services.result_delivery` as second channel (follow-up NC).

## Risks and Mitigations

- Risk: Caller number may be withheld or malformed.
  - Mitigation: Validate E.164-ish format, emit `skipped` status.
- Risk: Twilio transient failures.
  - Mitigation: Non-blocking delivery semantics; graph still completes.
- Risk: Delivery side-effects become hidden.
  - Mitigation: Always persist `delivery_result` in state and logs.

## Frozen Decisions

1. SMS is gated behind `SMS_AUTO_SEND=true`.
2. Triage SMS does not include a clinic callback SLA line in Phase 1.
3. Failed delivery remains graph-local in Phase 1 (no dedicated FSM event).

## Implementation Checklist

- [ ] Add `send_results` tool/node/state wiring in `graphs/medical_triage/graph.yaml`
- [ ] Update `mark_complete()` in `graphs/medical_triage/medical_triage.py`
- [ ] Pass `sms_to` from navigator coordinator graph action variables
- [ ] Add `projects/voice_runtime/transports/twilio_sms.py`
- [ ] Add/extend runtime transport factory for SMS dispatch
- [ ] Add `services/sms_formatter.py`
- [ ] Add `services/result_delivery.py`
- [ ] Add NC-193 unit and integration tests
- [ ] Run targeted test suite and record evidence

---

## Judgement (v2 — re-judged after plan revision)

**Verdict: APPROVED — the runtime boundary revision is a clear improvement. One prior amendment remains; one new amendment added; one prior amendment dropped; A1 is now resolved.**

### What changed from v1

The revision moves Twilio SMS transport from `ninchat_voice/services/sms_service.py` to `voice_runtime/transports/twilio_sms.py` and adds D6 (runtime transport factory extension). This is the correct call:

1. **voice_runtime already owns all Twilio SDK imports.** `twilio_call.py` imports `from twilio.rest import Client`; `twilio_ws.py` handles Twilio Media Streams. Adding `twilio_sms.py` alongside them is the natural placement. Zero new Twilio imports in ninchat_voice.

2. **Cross-project import pattern is established.** `ninchat_voice` already imports `from voice_runtime.transports.twilio_ws import register_voice_websocket` and `from voice_runtime.transports.twilio_call import build_stream_xml`. Adding `from voice_runtime.transports.twilio_sms import send_sms` follows the exact same convention.

3. **Test boundary is correct.** Provider-specific tests (`test_twilio_sms.py`) live in `voice_runtime/tests/` alongside `test_twilio_call.py` and `test_twilio_ws.py`. Orchestration tests (`test_result_delivery.py`) live in `ninchat_voice/tests/` where the delivery logic is.

### What passes inspection

1. **Graph-level delivery (Option A) still correct.** No change from v1.

2. **Non-blocking error semantics still correct.** Three-status contract (`sent|skipped|failed`) unchanged.

3. **Edge insertion point still precise.** `mark_complete → send_results → generate_farewell → END` unchanged.

4. **Runtime boundary is now the right shape.** voice_runtime owns transport (Twilio SDK, env vars, provider metadata normalization). ninchat_voice owns orchestration (when to send, what to format, what state to update). This is a clean separation.

5. **Test plan correctly split across projects.** Runtime transport tests in voice_runtime. Formatter, orchestrator, and graph wiring tests in ninchat_voice.

6. **Scope boundary unchanged and well-drawn.**

### Amendment status

**A1. Resolve Open Questions — RESOLVED.**

Frozen decisions are now set in this FR:

1. **SMS_AUTO_SEND gate:** Yes — gate behind `SMS_AUTO_SEND=true` env var. Default disabled. `send_results` checks this; if unset/false, returns `{status: "skipped", reason: "SMS_AUTO_SEND disabled"}`.

2. **Clinic callback SLA line:** No — omit for Phase 1.

3. **Failed delivery FSM event:** No — graph-local for Phase 1.

**A2. `mark_complete` phase invariant — STILL REQUIRED (carried from v1).**

Document that `phase: done` is an observability/delivery signal, not an edge routing target. Add comment when implementing.

**A3. SMS encoding boundary — DROPPED.**

On reflection, this was over-specified. Twilio handles multi-segment SMS transparently and bills per segment. Targeting a single 70-char UCS-2 segment would make the message too short to be useful for a Finnish clinical triage summary. The formatter should produce a clear, concise message without an artificial character limit. If cost becomes a concern, it's a product decision for a follow-up NC, not a framework constraint.

**A4. D6 factory extension must stay minimal — NEW.**

The current `create_transport()` in [transport.py](projects/voice_runtime/transport.py) returns a *module reference* for WebSocket registration. SMS is a fundamentally different interaction pattern (sync REST call, not async stream). Do not force SMS into the same factory return type. Two acceptable options:

- **(a) Separate accessor:** Add `get_sms_transport(provider="twilio")` returning the `twilio_sms` module. Callers use `twilio_sms.send_sms(to, body)`.
- **(b) No factory at all:** `result_delivery.py` imports `from voice_runtime.transports.twilio_sms import send_sms` directly. Factory is premature until a second SMS provider exists.

Option (a) is preferred. Twilio SMS is a POC-only provider and does not satisfy EU-based SMS needs, so selecting via `get_sms_transport(...)` keeps provider switch explicit and low-risk when an EU-capable SMS backend is introduced.

### Risks accepted

- **Caller number withheld:** Handled by `skipped` status. E.164-ish validation sufficient.
- **Twilio transient failures:** No retry in Phase 1. Single attempt, structured failure.
- **Cross-project API contract:** Narrow surface (`send_sms(to, body) -> dict`). Runtime transport test validates the contract. Drift risk is low given the 15-line function.

### Effort assessment

3 days remains realistic. D6 factory elimination removes one step; runtime transport test adds one. Net neutral.

- Day 1: TDD for `voice_runtime/transports/twilio_sms.py` + `ninchat_voice` `sms_formatter.py` and `result_delivery.py` (Red → Green)
- Day 2: Graph wiring (`graph.yaml` node/edges), coordinator bridge (`sms_to` variable), `mark_complete` update
- Day 3: Integration test, staging validation, observation

**Authority granted. Freeze scope. Begin enforcement with amendments A2 and A4 incorporated. A1 resolved; A3 dropped.**
