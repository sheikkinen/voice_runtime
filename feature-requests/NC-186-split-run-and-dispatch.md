# NC-186: Split _run_and_dispatch to Reduce Cyclomatic Complexity

**Priority:** MEDIUM
**Type:** Refactor
**Status:** Judged
**Effort:** 0.5 days
**Requested:** 2026-03-25
**Judged:** 2026-03-25

## Summary

Split `_run_and_dispatch` (CC=30, grade D) and `YamlgraphAsyncAction.execute` (CC=21, grade D) in `actions/real/yamlgraph_async_action.py` into focused helper functions. Target: no individual function at grade D; grade C (CC ≤ 15) acceptable.

## Value Statement

The most complex function in any voice project (CC=30) contains three distinct responsibilities mixed into one sequential flow. Splitting them makes each path independently testable, reviewable, and modifiable without fear of breaking unrelated logic.

## Problem

`_run_and_dispatch` (106→242, CC=30) handles:

1. **Graph execution** (lines 118–147): Load graph, detect resume vs fresh start, invoke `run_graph_async`
2. **Interrupt resolution** (lines 150–189): Check post-run state for interrupt/done, build payload
3. **Legacy event resolution** (lines 192–207): event_map lookup, route extraction for non-interrupt graphs
4. **Telemetry + dispatch** (lines 209–242): timing, logging, UI activity, socket send, error handling

`YamlgraphAsyncAction.execute` (CC=21) handles:

1. **Guard logic** (lines 258–268): stale cleanup, guard check
2. **Param extraction** (lines 270–302): graph path, keys, event_map, template resolution
3. **State snapshot** (lines 304–314): variables injection, thread_id resolution
4. **Task launch** (lines 316–340): logging, create_task, guard set

## Approach

### R1: Extract `_resolve_graph_event` from `_run_and_dispatch`

Extract the interrupt + legacy event resolution block (lines 150–207) into:

```python
async def _resolve_graph_event(
    app, run_config, result, thread_id, output_key, event_key,
    event_map, success_event,
) -> tuple[str, dict[str, Any]]:
    """Determine FSM event and payload from graph execution result.

    Returns (event_name, payload_dict).
    """
```

J-1 amendment: must be `async def` because it awaits `app.aget_state(run_config)` for interrupt detection.

This handles: interrupt detection (state.next set), done detection (event_map "done"), and legacy event_map/route fallback. One function, one responsibility: "what event should we send?"

### R2: Extract `_build_graph_input` from `_run_and_dispatch`

Extract the resume-vs-fresh-start detection (lines 128–147) into:

```python
async def _build_graph_input(
    app, run_config, initial_state, input_key,
) -> dict | Any:
    """Detect interrupt resume vs fresh start, return appropriate graph input."""
```

### R3: Extract `_snapshot_params` from `execute`

Extract param extraction + variable resolution (lines 270–314) into:

```python
def _snapshot_params(
    params: dict, context: dict[str, Any], current_state: str,
) -> dict[str, Any]:
    """Snapshot all inputs from context at action time.

    Raises ValueError when graph_path is missing (J-3).

    Returns dict with: graph_path, input_key, input_value, output_key,
    event_key, event_map, success_event, failure_event, phase,
    resolved_path, initial_state, thread_id.
    """
```

J-3 amendment: raises `ValueError` on missing `graph_path`. Caller (`execute()`) catches and returns failure event.

### Expected CC after refactoring

| Function | Before | After |
|---|---|---|
| `_run_and_dispatch` | 30 | ~10 (execute + dispatch + error, delegates to R1 and R2) |
| `_resolve_graph_event` | — | ~12 (interrupt + done + legacy, 3 code paths) |
| `_build_graph_input` | — | ~4 (resume check with fallback) |
| `execute` | 21 | ~8 (guard + snapshot + launch) |
| `_snapshot_params` | — | ~8 (param extraction + template resolve) |

No individual function exceeds grade C (CC ≤ 15). J-2 amendment: grade C acceptable.

## Acceptance Criteria

- [ ] `radon cc actions/real/yamlgraph_async_action.py -s -n C` shows no grade D (CC ≤ 20)
- [ ] Ideally no grade C either (CC ≤ 10), but grade C is acceptable
- [ ] `pytest tests/test_server_fsm_bridge.py tests/test_voice_actions_bridge.py` passes unchanged
- [ ] `ruff check actions/real/yamlgraph_async_action.py` clean
- [ ] No behavioral change — pure extract-method refactoring

## Risks

- The background task captures `context` by reference (mutable dict). Extracting helpers must preserve this shared-reference semantics.
- `_run_and_dispatch` is awaited via `asyncio.create_task` — extracted async helpers must remain `async def` where they use `await`.
- Test patches targeting `_run_and_dispatch` or `yamlgraph_async_action.py` internal names may need updating.

---

## Judgement

**Verdict: Approved with amendments.**

The decomposition targets are correct and the extraction boundaries align with natural responsibility seams in the code. Test risk is low — existing tests patch `yamlgraph.executor_async.*` (external), not internal helpers. Three amendments required before enforcement:

### J-1: `_resolve_graph_event` must be `async def`

The code at lines 156–159 calls `await app.aget_state(run_config)`. The FR proposes a regular `def` signature. This function must be `async def` because it needs to await the compiled graph's state snapshot to detect interrupt vs completion.

```python
async def _resolve_graph_event(
    app, run_config, result, thread_id, output_key, event_key,
    event_map, success_event,
) -> tuple[str, dict[str, Any]]:
```

### J-2: Fix CC target contradiction

The Summary says "targeting CC ≤ 10 (grade A/B)" but the Expected CC table shows R1 at ~12 (grade C). These contradict. Amend the Summary to: "targeting no individual function at grade D; grade C (CC ≤ 15) acceptable." This matches the acceptance criteria which already allow grade C.

### J-3: `_snapshot_params` must handle missing `graph_path`

The current `execute()` has an early return at line 274 when `graph_path` is missing:

```python
if not graph_path:
    logger.error("yamlgraph_async: no graph specified in params")
    return params.get("failure", "failed")
```

If `_snapshot_params` is extracted, this early-return path becomes unreachable from `execute()`. The FR must specify: `_snapshot_params` raises `ValueError` when `graph_path` is absent, and `execute()` catches it and returns the failure event. This keeps the error loud (commandment 6) while preserving the existing behavior.

```python
# In _snapshot_params:
if not graph_path:
    raise ValueError("no graph specified in params")

# In execute:
try:
    snap = _snapshot_params(params, context, current_state)
except ValueError as exc:
    logger.error("yamlgraph_async: %s", exc)
    return params.get("failure", "failed")
```

### Test impact assessment

Verified: all 10+ tests in `test_nc138_async_action_classes.py` patch `yamlgraph.executor_async.load_and_compile_async` and `yamlgraph.executor_async.run_graph_async` — both external module paths unaffected by internal extraction. No tests directly reference `_run_and_dispatch` in patch targets. The acceptance criterion "tests pass unchanged" is achievable.

### Scope freeze

Authority granted for R1 + R2 + R3 with the three amendments above. No new public API, no new files, no behavioral changes. All extracted functions remain module-private (underscore-prefixed) in the same file.
