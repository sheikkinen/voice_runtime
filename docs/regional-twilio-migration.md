# Regional Twilio REST migration — 0.1.14

These APIs are independent. Configuration and consumer policy are explicit;
neither reads regional key names nor discovers regions. No live calls were used
to validate these examples: the tests replace only the final HTTP send.

## VR-004: hang up using ordered regional credentials

```python
from voice_runtime.transports.twilio_call import (
    TwilioCallNotFoundError,
    TwilioRegion,
    hangup_call,
)


def finish_call(
    call_sid: str,
    account_sid: str,
    us1_auth: tuple[str, str],
    ie1_auth: tuple[str, str],
) -> None:
    regions = (
        TwilioRegion(api_host="api.twilio.com", account_sid=account_sid, auth=us1_auth),
        TwilioRegion(api_host="api.dublin.ie1.twilio.com", account_sid=account_sid, auth=ie1_auth),
    )
    try:
        hangup_call(call_sid, regions=regions)
    except TwilioCallNotFoundError:
        # Absence in every supplied region is NOT terminal evidence.
        # Let the calling application's failure policy handle it.
        raise
```

Supply each API-key SID/secret pair separately from the account SID used in the
URL path. No account auth token is needed for this explicit REST operation.
`TwilioRegion` is frozen; auth is hidden in its repr and validation messages.
Do not log model dumps: explicit serialization still contains authentication.

- Only 404/20404 advances to the next region. At most one request per host.
- 2xx and 400/21220 stop successfully. Other errors/timeouts propagate immediately.
- Exhaustion raises `TwilioCallNotFoundError`, whose `attempted_hosts` is ordered.
- `hangup_call(call_sid)` still uses ambient account-token/SDK routing, but its
  404/20404 now raises with the actual request hostname; other 404 codes propagate.
  The existing media handler catches this and closes its WebSocket immediately.
  Webhook signing and the media handler's token precheck are unchanged.

## VR-006: fetch one complete region

```python
from datetime import datetime

from voice_runtime.transports.twilio_call import (
    TwilioCallListIncompleteError,
    TwilioCallRow,
    list_calls,
)


def read_region(
    api_host: str,
    account_sid: str,
    auth: tuple[str, str],
    destination: str,
    cutoff: datetime,
) -> list[TwilioCallRow]:
    try:
        return list_calls(
            api_host=api_host,
            account_sid=account_sid,
            auth=auth,
            to=destination,
            start_after=cutoff,
            page_size=200,
            max_pages=10,
        )
    except TwilioCallListIncompleteError:
        # The region failed; never substitute [] or persist a partial snapshot.
        raise
```

`cutoff` must be timezone-aware. The first request carries a UTC **instant**, not
just a date. Known timestamps at/before that instant are excluded locally.
Rows expose exactly `call_sid`, `status`, `start_time`, `direction`, `caller`.
`start_time` is `float | None`: null/missing time is retained as unknown, never
zero. Callers **must skip unknown-time rows when creating age-based stubs**.
Direction and anonymous caller labels are retained; blank caller becomes `None`.
The consumer, not this library, owns leg eligibility and cross-region health.

Every page must be well formed. Continuation must stay on HTTPS, the exact host
and account Calls collection, without a port, userinfo or fragment. The SDK
`get_page` follows the validated URL without replacing its query/PageToken.
Redirects fail without following `Location`. A complete empty response is `[]`;
cap exhaustion or any page/row/transport error raises and discards accumulated rows.

`list_recent_calls(lookback_s=3600.0)` remains unchanged except for deprecation
documentation: ambient configuration, dictionaries, zero for absent start time,
`limit=200`, and missing-credential `[]`. Migration is deliberate, not automatic.

## Shared constraints and release boundary

- Hosts: `api.twilio.com` or `api.<edge>.<region>.twilio.com`, lowercase DNS labels.
  SIDs: nonblank strict ASCII alphanumeric strings (no URI delimiters/escapes).
- Explicit APIs ignore ambient credentials/region/edge without mutating the
  environment. Only `TWILIO_HTTP_TIMEOUT` is read; default 15 seconds, finite
  positive overrides only. No retries. This is a per-request timeout, **not a
  hard total-operation deadline**.
- Requires `pydantic>=2.0,<3` directly. All six symbols are exported from
  `voice_runtime.transports.twilio_call`; no consumer SDK import is needed.
- 0.1.14 is prepared, not published by this change. No tags, releases, deployment,
  consumer pins or credentials are modified. Consumer enforcement must separately
  adopt typed fields, unknown-time exclusion and failed-region handling.