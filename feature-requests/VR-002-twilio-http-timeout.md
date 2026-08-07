# VR-002 — Bound the Twilio REST HTTP timeout

**Status:** APPROVED WITH REVISIONS (2026-08-07) — R-1..R-4 folded below; C-1..C-5 in
`VR-002-twilio-http-timeout.judgement.md` gate enforcement
**Judged:** 2026-08-07
**Repo:** sheikkinen/voice_runtime — GitHub issue [#2](https://github.com/sheikkinen/voice_runtime/issues/2)
**Component:** `voice_runtime/transports/twilio_sms.py`, `voice_runtime/transports/twilio_call.py`
**Version observed:** 0.1.10
**Severity:** High (latent) — an unbounded block on a live call path freezes worker
teardown and starves the supervisor pool
**Consumer:** csap `VBOT-76` AC-10 pins the release carrying this fix
**Numbering note:** `VR-xxx` = defect raised by this repo's own issue tracker. The
`NC-xxx` series is allocated by `ninchat_voice` (now at NC-401) and csap now
numbers `VBOT-xxx`; minting an NC number here would race the consumer repo.

---

## Problem

Every Twilio REST call in this package constructs `twilio.rest.Client(sid, token)`
with no `http_client`. The SDK's default HTTP client applies **no request
timeout**, so a network path that silently drops packets (SYN blackhole — no RST,
no ICMP) blocks the synchronous call for the OS TCP connect timeout, ~2 minutes.

Four unbounded construction sites:

| Site | Function | Path |
|------|----------|------|
| `twilio_sms.py:30` | `send_sms` | delivery seam — moving to call teardown in csap VBOT-76 |
| `twilio_call.py:104` | `initiate_outbound_call` | outbound call start (outcaller) |
| `twilio_call.py:129` | `hangup_call` | **call teardown** (NC-362) |
| `twilio_call.py:150` | `list_recent_calls` | CDR reconciler (NC-395) |

Observed from csap (ninchat_voice) on GKE TEST: the current egress block fails
fast (RST → `ConnectionResetError` in ~1.1 s), so the hazard is **latent today** —
it materialises the moment egress policy changes to a drop/blackhole rule, or the
path traverses a firewall that blackholes rather than rejects. Because
`hangup_call` and the VBOT-76 SMS send both run on the call-teardown path, a
2-minute block does not merely delay one send: it holds the worker slot, and with
`WORKER_POOL_SIZE=3` three such blocks exhaust the pool and new calls get
`503 BUSY_TWIML`.

This is a **boundary defect**: an external system's failure mode (silent packet
drop) is admitted into our process without a bound. The normalization belongs at
the client-construction boundary, not at each call site.

---

## Prior art (R-4)

| FR | Relation | Disposition |
|----|----------|-------------|
| NC-154 transport intent abstraction | Establishes `voice_runtime/transports/` as the boundary owning protocol-specific execution | **Supports** — the timeout is a transport-protocol detail, so it belongs here, not in consumers |
| NC-193 triage result delivery / SMS service | Made SMS delivery runtime-owned to keep the Twilio SDK out of consumers | **Supports** — that decision is exactly why the missing bound is voice_runtime's defect to fix |
| NC-285 route-token stream XML | Keeps Twilio wire details inside `twilio_call.py` | **Supports** — same module, same containment principle; no overlap in surface |
| NC-271 mock transport bridge | Explicitly leaves *real* Twilio provider failure simulation out of mock scope | **Bounds this FR** — confirms the regression must be a deterministic no-network unit test, not a mock-bridge scenario |

No prior FR (approved or rejected) proposed bounding the Twilio REST client, so
this is new territory rather than a re-entry.

---

## Objective

Every Twilio REST request issued by voice_runtime completes or fails within a
bounded, configurable wall-clock budget — default 15 s — so no consumer call path
can be blocked longer than that by network conditions.

---

## Constraints

- **No silent fallbacks.** A timeout must surface as an exception to the caller;
  do not swallow it or return a fake success dict.
- **No new public API.** The timeout is an internal construction detail plus one
  env var; `send_sms()` / `hangup_call()` / `initiate_outbound_call()` /
  `list_recent_calls()` signatures are unchanged.
- **One construction boundary.** All four sites must go through a single internal
  helper — a per-site fix leaves the next Twilio call unguarded
  (`partial_remediation`).
- **15 s default**, overridable via `TWILIO_HTTP_TIMEOUT` (float seconds), to
  match the Ninchat `_post_json` cap used by the same delivery seam in csap.
- **Deterministic test at $0** — no network, no live Twilio credentials. Assert on
  the constructed client's configuration, not on observed latency.
- Lazy `from twilio.rest import Client` inside functions is preserved (the SDK
  stays an optional import; the module must remain importable without it).

---

## Acceptance Criteria

1. **Bounded by default.** With `TWILIO_HTTP_TIMEOUT` unset, each of the four
   entry points constructs its `Client` with an HTTP client carrying a 15.0 s
   timeout. Asserted by unit test on the constructed object (no network).
2. **Configurable.** `TWILIO_HTTP_TIMEOUT=3` yields a client whose HTTP timeout is
   `3.0`. Invalid/unparseable values raise at construction rather than silently
   reverting to unbounded.
3. **(R-1) Single boundary.** A test asserts that no module under
   `voice_runtime/transports/` constructs `twilio.rest.Client(...)` directly
   **except the shared helper module itself**, which is the sole authorized
   construction point. (Static assertion over the source or a patched-helper
   call-count test; the point is that a future fifth call site inherits the bound.)
4. **(R-2) Timeout surfaces at request time.** A timeout raised by the *request*
   — `messages.create(...)` in `send_sms`, `calls(...).update(...)` in
   `hangup_call` — propagates to the caller unchanged: no swallowing, no `None`
   return, no retry loop added by this FR. Asserting only that a construction-time
   exception escapes is **not** the witness.
5. **(R-3) Credential behaviour preserved, both variants.** Existing
   `tests/test_nc193_twilio_sms.py` and `tests/test_twilio_call.py` pass unmodified,
   and the two distinct pre-client behaviours are each asserted:
   - `send_sms`, `initiate_outbound_call`, `hangup_call` raise `RuntimeError`
     **before** any client is constructed when credentials are absent;
   - `list_recent_calls` returns `[]` **without** constructing a client when
     credentials are absent (reconciliation is a deliberate no-op off-fly).
6. **Optional import preserved.** No module-level import of `twilio.rest.Client`
   or `twilio.http.http_client.TwilioHttpClient`; `voice_runtime` stays importable
   without the Twilio SDK until a Twilio-backed function is called.

---

## Implementation Approach (TDD)

### RED — failing tests first

Add `tests/test_vr002_twilio_http_timeout.py`, tagged `@pytest.mark.req("VR-002")`,
patching `twilio.rest.Client` with a `MagicMock` and inspecting the `http_client`
kwarg (follow the `patch`/`MagicMock` pattern already in
`tests/test_nc193_twilio_sms.py`):

- `test_send_sms_client_has_default_timeout` — AC-1 for the SMS site.
- `test_hangup_call_client_has_default_timeout` — AC-1 for the teardown site.
- `test_initiate_outbound_call_client_has_default_timeout` /
  `test_list_recent_calls_client_has_default_timeout` — AC-1 for the remaining two.
- `test_timeout_env_override` — `TWILIO_HTTP_TIMEOUT=3` → `3.0` (AC-2).
- `test_invalid_timeout_raises` — `TWILIO_HTTP_TIMEOUT=abc` raises (AC-2).
- `test_no_direct_client_construction_in_transports` — AC-3 boundary witness,
  exempting the helper module.
- `test_request_timeout_propagates_from_send_sms` /
  `test_request_timeout_propagates_from_hangup_call` — the mocked
  `messages.create(...)` / `calls(...).update(...)` raise the SDK timeout
  exception; assert it escapes unchanged (AC-4, R-2).
- `test_list_recent_calls_no_credentials_constructs_no_client` — asserts `[]` and
  zero helper calls (AC-5, R-3).

All fail on `main` (current clients carry no `http_client`).

### GREEN — minimal change

Add one internal helper, e.g. `voice_runtime/transports/_twilio_client.py`:

```python
def build_twilio_client(account_sid: str, auth_token: str):
    """Twilio REST client with a bounded request timeout (default 15s)."""
    from twilio.http.http_client import TwilioHttpClient
    from twilio.rest import Client

    timeout = float(os.environ.get("TWILIO_HTTP_TIMEOUT", "15"))
    return Client(account_sid, auth_token, http_client=TwilioHttpClient(timeout=timeout))
```

Replace all four `Client(account_sid, auth_token)` constructions with
`build_twilio_client(account_sid, auth_token)`. The existing pre-client guards
stay ahead of construction unchanged — `RuntimeError` for the three raising
functions, early `return []` for `list_recent_calls` (AC-5).

### REFACTOR

None expected. Do not introduce retry, backoff, or async wrapping in this FR.

---

## Out of Scope

- Retry/backoff on Twilio failures (a timeout that fires is a real failure; the
  consumer decides).
- Making the SMS send asynchronous or moving it off the call path — that is csap
  VBOT-76's job; this FR only bounds the blocking window.
- `httpx.Client` in `mock_bridge.py` (already bounded at 10.0 s).
- Twilio WebSocket transport (`twilio_ws.py`) — no REST client involved.

---

## Release / Consumer Impact

- Ships as **0.1.11**; `CHANGELOG.md` fragment under `fix(twilio)`.
- **(C-4) Consumer pin bumps are NOT authorized under this FR** — recorded here as
  the follow-up only: csap `requirements-deploy.txt` / `pyproject.toml` (currently
  `0.1.10`, VBOT-76 AC-10), ninchat_voice (currently `0.1.9`, already one release
  behind), outcaller. Each needs its own authority in its own repo.
- No consumer code change required — env var is optional.
