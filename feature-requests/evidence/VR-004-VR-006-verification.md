# VR-004 / VR-006 implementation verification — 2026-09-08

**Prior art:** the two governing FRs and their round-2 judgements; VR-002's
single bounded client boundary and VR-003's media exception path.

## Identity and RED/GREEN trail

Base: `67cfa28`, docs PR #4 merged. Runtime implementation at `96cba6e`,
plus release metadata 0.1.14 and migration examples in the subsequent handoff
commit. No live Twilio calls, credential reads or billable requests.

| Scope | Behavioral RED | GREEN | Witness |
|---|---|---|---|
| VR-004 | `599aedd` | `180c877` | 76 failed / 13 passed, then full suite 402 passed / 1 skipped |
| VR-006 | `53402bc` | `96cba6e` | 180 failed / 5 passed, then full suite 587 passed / 1 skipped |

RED failures were behavioral, not collection/import errors. The judgements
permit minimal importable API skeletons before RED. Each FR has a separate
requirement-marked suite and independent migration-example test.

## Final checks

- `.venv/bin/python -m pytest tests/ -q --no-cov`: **589 passed, 1 skipped,
  9 warnings**, 9.20 seconds. Warnings include Starlette's HTTPX deprecation
  and unawaited coroutines in the session/STT mock tests; no warning filters
  were introduced. This is not a warning-clean claim.
- Ruff default rules and format checks passed for the changed transport
  modules and migration tests, using `--isolated` to avoid the enclosing
  YAMLGraph repository's unrelated configuration. No code suppressions added.
- `radon cc voice_runtime/transports/ -s -n D`: no grade D or worse.
  Call module 287 lines; model module 145 lines.
- `git diff --check`: passed before handoff.

## Clean distribution witness

Built wheel: `voice_runtime-0.1.14-py3-none-any.whl`.
SHA-256: `92ebe2981e470a675abb2ffd4f8ec3bef88f75660ce2a7ef0ebd0f27c64c5485`.
This hash identifies the local pre-handoff verification build, not a published
artifact. Installed into a new venv with system site packages disabled; ran
outside the source tree with Python `-I` and environment cleared.

- Metadata: version 0.1.14; Pydantic 2.13.5; Twilio SDK 9.11.0.
- Only new direct dependency: `pydantic>=2.0,<3`.
- Module origin was within that venv's site-packages; installation was not
  editable. `pip check`: **No broken requirements found.**
- VR-004 smoke imported only `hangup_call`, `TwilioRegion`,
  `TwilioCallNotFoundError`; two fake HTTP sends proved US1 absence then
  Dublin success, exact account path, timeout 15 and redirects disabled.
- VR-006 smoke imported only `list_calls`, `TwilioCallRow`,
  `TwilioCallListIncompleteError`; two fake pages proved preserved
  `PageToken=A%2B`, UTC-offset timestamp conversion, typed rows, timeout 15
  and redirects disabled.

Local raw logs are under `tmp/verification/` and are not portable evidence;
committed tests reproduce the behavioral contracts. The migration guide's
two code examples are executed directly by separate tests, using only dummy
credentials and a replacement final HTTP send.

## Remaining gates

Independent PR review and human merge; actual tag/package publication;
consumer plan alignment, exact pins, owner-applied credentials and rollout.
Neither a passing local build nor this record claims a live regional call
was ended or any deployed reconciler was repaired.