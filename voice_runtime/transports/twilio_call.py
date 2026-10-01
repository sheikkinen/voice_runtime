"""Twilio REST call management — outbound initiation and TwiML generation.

NC-154: Extracted from outcaller/nodes/twilio_call.py. All Twilio SDK
usage lives here — consumers never import twilio directly.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime
from urllib.parse import quote, urljoin, urlsplit

from ._twilio_client import build_explicit_twilio_client, build_twilio_client
from ._twilio_models import (
    TwilioCallListIncompleteError as TwilioCallListIncompleteError,
)
from ._twilio_models import TwilioCallNotFoundError as TwilioCallNotFoundError
from ._twilio_models import TwilioCallRow as TwilioCallRow
from ._twilio_models import TwilioRegion as TwilioRegion
from ._twilio_models import _CallPage, validate_sid

logger = logging.getLogger(__name__)


def _get_twilio_env() -> tuple[str, str, str, str]:
    """Read Twilio environment variables.

    Returns:
        (account_sid, auth_token, phone_number, stream_url)
    """
    return (
        os.getenv("TWILIO_ACCOUNT_SID", ""),
        os.getenv("TWILIO_AUTH_TOKEN", ""),
        os.getenv("TWILIO_PHONE_NUMBER", ""),
        os.getenv("VOICE_STREAM_URL", ""),
    )


def build_stream_twiml(stream_url: str) -> str:
    """Build TwiML XML that connects Twilio to a Media Streams WebSocket.

    Args:
        stream_url: Public URL pointing at the /voice WebSocket endpoint.
            May be https:// or http:// — converted to wss:// or ws://.

    Returns:
        TwiML XML string.
    """
    ws_url = stream_url.replace("https://", "wss://").replace("http://", "ws://")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{ws_url}/voice" />
    </Connect>
</Response>""".strip()


# Alias for consumers that don't want 'twiml' in their import line
build_stream_xml = build_stream_twiml


def build_route_stream_xml(stream_url: str, route_token: str) -> str:
    """Build XML that connects Twilio to an opaque routed stream endpoint.

    Args:
        stream_url: Public base URL. May be https:// or http:// — converted to
            wss:// or ws://.
        route_token: Opaque per-call route token appended under /voice/.

    Returns:
        XML string for Twilio Media Streams.
    """
    ws_base = (
        stream_url.rstrip("/").replace("https://", "wss://").replace("http://", "ws://")
    )
    safe_token = quote(route_token, safe="")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{ws_base}/voice/{safe_token}" />
    </Connect>
</Response>""".strip()


def initiate_outbound_call(phone: str) -> str:
    """Initiate an outbound Twilio call with Media Streams.

    Args:
        phone: Phone number to call (E.164 format).

    Returns:
        call_sid from Twilio.

    Raises:
        RuntimeError: If VOICE_STREAM_URL or Twilio credentials are missing.
    """
    account_sid, auth_token, phone_number, stream_url = _get_twilio_env()

    if not stream_url:
        raise RuntimeError(
            "Set VOICE_STREAM_URL to a public WebSocket URL (use ngrok for local dev)"
        )
    if not account_sid or not auth_token:
        raise RuntimeError("TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN required")
    if not phone_number:
        raise RuntimeError("TWILIO_PHONE_NUMBER required")

    twiml = build_stream_twiml(stream_url)
    logger.info("Initiating outbound call to %s", phone)

    client = build_twilio_client(account_sid, auth_token)
    call = client.calls.create(
        to=phone,
        from_=phone_number,
        twiml=twiml,
    )
    logger.info("Call initiated: call_sid=%s", call.sid)
    return call.sid


def _validated_regions(regions: object) -> tuple[TwilioRegion, ...]:
    """Revalidate an explicit region tuple; reject empty, non-tuple, duplicates."""
    if not isinstance(regions, tuple) or not regions:
        raise ValueError("regions must be a nonempty tuple")
    validated = tuple(TwilioRegion.model_validate(region) for region in regions)
    hosts = tuple(region.api_host for region in validated)
    if len(set(hosts)) != len(hosts):
        raise ValueError("Region hosts must be distinct")
    return validated


def hangup_call(
    call_sid: str, *, regions: tuple[TwilioRegion, ...] | None = None
) -> None:
    """Complete a call, trying explicit regions only after 404/20404 absence.

    Only a successful update or 400/21220 proves terminal state. Exhaustion
    raises TwilioCallNotFoundError. Without regions, preserve ambient SDK
    routing and account-token authentication, but never treat absence as success.
    """
    if regions is not None:
        validate_sid(call_sid)
        validated = _validated_regions(regions)
        for region in validated:
            if _complete_call(build_explicit_twilio_client(region), call_sid):
                return
        raise TwilioCallNotFoundError(tuple(region.api_host for region in validated))

    account_sid, auth_token, _phone_number, _stream_url = _get_twilio_env()
    if not account_sid or not auth_token:
        raise RuntimeError("TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN required")
    client = build_twilio_client(account_sid, auth_token)
    if not _complete_call(client, call_sid):
        host = urlsplit(client.get_hostname(client.api.base_url)).hostname
        raise TwilioCallNotFoundError((host,))


def _complete_call(client, call_sid: str) -> bool:
    """Return false only on classified regional absence; propagate other errors."""
    from twilio.base.exceptions import TwilioRestException

    try:
        client.calls(call_sid).update(status="completed")
    except TwilioRestException as exc:
        if exc.status == 404 and exc.code == 20404:
            return False
        if exc.status == 400 and exc.code == 21220:
            logger.info(
                "Call already terminal: call_sid=%s (status=%s code=%s)",
                call_sid,
                exc.status,
                exc.code,
            )
            return True
        raise
    logger.info("Call hangup issued: call_sid=%s", call_sid)
    return True


def list_recent_calls(lookback_s: float = 3600.0) -> list[dict]:
    """READ-ONLY CDR fetch for reconciliation (ninchat_voice NC-395).

    Deprecated: migrate explicitly to list_calls for complete regional reads.

    Returns [{"call_sid", "status", "start_time" (epoch)}] for inbound
    calls to our number within the lookback window. Returns [] when
    credentials are absent — reconciliation is a no-op off-fly.
    """
    from datetime import UTC, datetime

    account_sid, auth_token, phone_number, _stream_url = _get_twilio_env()
    if not account_sid or not auth_token:
        return []
    import time as _time

    after = datetime.fromtimestamp(_time.time() - lookback_s, tz=UTC)
    calls = build_twilio_client(account_sid, auth_token).calls.list(
        to=phone_number or None, start_time_after=after, limit=200
    )
    return [
        {
            "call_sid": c.sid,
            "status": str(c.status or ""),
            "start_time": c.start_time.timestamp() if c.start_time else 0,
        }
        for c in calls
    ]


def list_calls(
    *,
    api_host: str,
    account_sid: str,
    auth: tuple[str, str],
    to: str,
    start_after: datetime,
    page_size: int = 200,
    max_pages: int = 10,
) -> list[TwilioCallRow]:
    """Fetch a complete, destination-filtered, explicit-region call list.

    Unknown timestamps remain None. Known times at/before start_after are
    excluded. No accumulated rows escape a page failure or continuation cap.
    """
    region = TwilioRegion(api_host=api_host, account_sid=account_sid, auth=auth)
    _validate_list_query(to, start_after, page_size, max_pages)
    after = start_after.astimezone(UTC)
    calls = build_explicit_twilio_client(region).calls
    page = calls.page(to=to, start_time_after=after, page_size=page_size)
    rows: list[TwilioCallRow] = []
    collection = f"/2010-04-01/Accounts/{account_sid}/Calls.json"
    pages_fetched = 0
    while True:
        pages_fetched += 1
        # Page construction enforces HTTP 200 and JSON decoding. Validate its
        # raw payload before CallInstance can erase malformed/offset timestamps.
        payload = _CallPage.model_validate(page._payload)
        for record in payload.calls:
            row = record.as_row()
            if row.start_time is None or row.start_time > after.timestamp():
                rows.append(row)
        if payload.next_page_uri is None:
            return rows
        target = _validated_next_url(payload.next_page_uri, api_host, collection)
        if pages_fetched == max_pages:
            raise TwilioCallListIncompleteError(api_host, pages_fetched)
        # Unlike next_page(), get_page accepts the already validated absolute
        # URL. It preserves the provider query without reapplying initial filters.
        page = calls.get_page(target)


def _validate_list_query(
    to: str, start_after: datetime, page_size: int, max_pages: int
) -> None:
    """Reject unfiltered or unbounded reads before constructing any client."""
    if not isinstance(to, str) or not to.strip():
        raise ValueError("to must be a nonblank string")
    if not isinstance(start_after, datetime) or start_after.utcoffset() is None:
        raise ValueError("start_after must be timezone-aware")
    if type(page_size) is not int or not 1 <= page_size <= 1000:
        raise ValueError("page_size must be an integer between 1 and 1000")
    if type(max_pages) is not int or max_pages < 1:
        raise ValueError("max_pages must be a positive integer")


def _validated_next_url(link: str, api_host: str, collection: str) -> str:
    """Allow only a well-formed same-authority, exact-collection continuation."""
    if (
        not link
        or any(ord(char) <= 32 or ord(char) >= 127 for char in link)
        or "\\" in link
        or "#" in link
        or re.search(r"%(?![0-9a-fA-F]{2})", link)
    ):
        raise ValueError("Malformed call continuation")
    parts = urlsplit(link)
    if (
        (parts.scheme and (parts.scheme != "https" or not parts.netloc))
        or (parts.netloc and parts.netloc != api_host)
        or any(segment in (".", "..") for segment in parts.path.split("/"))
    ):
        raise ValueError("Call continuation changes authority or path")
    target = urljoin(f"https://{api_host}{collection}", link)
    resolved = urlsplit(target)
    if (
        resolved.scheme != "https"
        or resolved.netloc != api_host
        or resolved.path != collection
    ):
        raise ValueError("Call continuation changes authority or collection")
    return target
