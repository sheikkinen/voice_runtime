"""VR-006: independent, complete-or-raise reads through real SDK HTTP seams."""

import base64
import inspect
import os
from datetime import UTC, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit
from unittest.mock import MagicMock

import pytest
from requests.exceptions import ReadTimeout
from twilio.base.exceptions import TwilioException

from tests.twilio_http_fakes import FakeTwilioHTTP
from voice_runtime.transports.twilio_call import (
    TwilioCallListIncompleteError,
    TwilioCallRow,
    list_calls,
    list_recent_calls,
)

HOSTS = ("api.twilio.com", "api.dublin.ie1.twilio.com")
COLLECTION = "/2010-04-01/Accounts/AC123/Calls.json"
NEXT = COLLECTION + "?PageToken=PA%2B%2F%3D&Page=1&PageSize=2&To=%2B15550001"
CUTOFF = datetime(2026, 9, 8, 6, 30, tzinfo=UTC)


@pytest.fixture
def wire(monkeypatch):
    return FakeTwilioHTTP(monkeypatch)


def fetch(**overrides):
    config = dict(
        api_host=HOSTS[0],
        account_sid="AC123",
        auth=("SKkey", "secret"),
        to="+15550001",
        start_after=CUTOFF,
    )
    config.update(overrides)
    return list_calls(**config)


def row(**overrides):
    payload = dict(
        sid="CA123",
        status="completed",
        direction="inbound",
        start_time="Tue, 08 Sep 2026 06:31:00 +0000",
        **{"from": "anonymous"},
    )
    payload.update(overrides)
    return payload


def page(*records, next_uri=None):
    return {"calls": list(records), "next_page_uri": next_uri}


@pytest.mark.req("VR-006")
@pytest.mark.parametrize("host", HOSTS)
def test_exact_filtered_request_ignores_ambient_routing_and_auth(
    wire, monkeypatch, host
):
    for key, value in {
        "TWILIO_ACCOUNT_SID": "ambient",
        "TWILIO_AUTH_TOKEN": "ambient",
        "TWILIO_PHONE_NUMBER": "wrong",
        "TWILIO_REGION": "au1",
        "TWILIO_EDGE": "sydney",
    }.items():
        monkeypatch.setenv(key, value)
    before = dict(os.environ)
    wire.reply(payload=page(row()))
    rows = fetch(
        api_host=host,
        start_after=CUTOFF.astimezone(timezone(timedelta(hours=3))),
        page_size=23,
    )
    assert rows == [
        TwilioCallRow(
            call_sid="CA123",
            status="completed",
            direction="inbound",
            start_time=CUTOFF.timestamp() + 60,
            caller="anonymous",
        )
    ]
    sent = wire.sent[0]
    url = urlsplit(sent.request.url)
    assert f"{url.scheme}://{url.netloc}{url.path}" == f"https://{host}{COLLECTION}"
    assert parse_qs(url.query) == {
        "To": ["+15550001"],
        "StartTime>": ["2026-09-08T06:30:00Z"],
        "PageSize": ["23"],
    }
    assert sent.request.method == "GET"
    assert (
        sent.request.headers["Authorization"]
        == "Basic " + base64.b64encode(b"SKkey:secret").decode()
    )
    assert sent.timeout == 15.0 and sent.allow_redirects is False
    assert dict(os.environ) == before


@pytest.mark.req("VR-006")
@pytest.mark.parametrize(
    "field,value",
    [
        ("account_sid", v)
        for v in (
            "",
            "AC/1",
            "AC\\1",
            "AC?1",
            "AC#1",
            "AC%2F1",
            "AC 1",
            "AC\n1",
            "AC\t1",
            "AC\x001",
            "ÄC1",
            "AC-1",
            12,
            None,
        )
    ]
    + [
        ("api_host", v)
        for v in (
            "https://api.twilio.com",
            "api.twilio.com:443",
            "u@api.twilio.com",
            "api.twilio.com/p",
            "api.twilio.com?q",
            "api.twilio.com#x",
            "API.twilio.com",
            "api.ie1.twilio.com",
            "api.x.ie1.other.com",
            "api.-x.ie1.twilio.com",
        )
    ]
    + [
        ("auth", v)
        for v in (
            ("SKkey", ""),
            (" ", "secret"),
            ("SKkey",),
            (12, "secret"),
            ["SKkey", "secret"],
            None,
        )
    ]
    + [("to", v) for v in ("", " \t", None, 123)]
    + [("start_after", v) for v in (datetime(2026, 9, 8), "2026-09-08", None)]
    + [("page_size", v) for v in (True, False, 0, -1, 1001, 1.5, "2", None)]
    + [("max_pages", v) for v in (True, False, 0, -1, 1.5, "2", None)],
)
def test_invalid_input_rejected_before_client(wire, field, value):
    with pytest.raises(ValueError) as caught:
        fetch(**{field: value})
    assert wire.constructed == 0 and wire.sent == []
    assert "secret" not in str(caught.value) and "SKkey" not in str(caught.value)


@pytest.mark.req("VR-006")
@pytest.mark.parametrize(
    "missing", ["api_host", "account_sid", "auth", "to", "start_after"]
)
def test_required_keywords_cannot_be_omitted(wire, missing):
    config = dict(
        api_host=HOSTS[0],
        account_sid="AC123",
        auth=("key", "secret"),
        to="+15550001",
        start_after=CUTOFF,
    )
    del config[missing]
    with pytest.raises(TypeError):
        list_calls(**config)
    assert wire.constructed == 0


@pytest.mark.req("VR-006")
def test_normalization_preserves_unknown_time_directions_and_exact_cutoff(wire):
    missing_time = row(sid="CAmissing")
    del missing_time["start_time"]
    wire.reply(
        payload=page(
            row(start_time="Tue, 08 Sep 2026 09:31:00 +0300"),
            row(
                sid="CAout",
                direction="outbound-api",
                start_time=None,
                **{"from": "withheld"},
            ),
            row(sid="CAblank", start_time=None, **{"from": " \t"}),
            row(sid="CAnull", start_time=None, **{"from": None}),
            missing_time,
            row(sid="CAequal", start_time="Tue, 08 Sep 2026 06:30:00 +0000"),
            row(sid="CAold", start_time="Tue, 08 Sep 2026 06:29:59 +0000"),
        )
    )
    rows = fetch()
    assert [r.call_sid for r in rows] == [
        "CA123",
        "CAout",
        "CAblank",
        "CAnull",
        "CAmissing",
    ]
    assert rows[0].start_time == CUTOFF.timestamp() + 60
    assert rows[1].direction == "outbound-api" and rows[1].caller == "withheld"
    assert all(r.start_time is None for r in rows[1:])
    assert rows[2].caller is None and rows[3].caller is None
    assert set(rows[0].model_dump()) == {
        "call_sid",
        "status",
        "start_time",
        "direction",
        "caller",
    }
    with pytest.raises(ValueError):
        rows[0].status = "changed"


@pytest.mark.req("VR-006")
@pytest.mark.parametrize(
    "field,value",
    [
        (f, v)
        for f in ("sid", "status", "direction")
        for v in (None, "", " ", 12, [], {})
    ]
    + [
        ("start_time", v)
        for v in ("", "nonsense", "Tue, 08 Sep 2026 06:31:00", 123, {}, [])
    ]
    + [("from", v) for v in (12, {}, [])],
)
@pytest.mark.parametrize("later", [False, True])
def test_malformed_rows_never_silently_skip_or_become_unknown(
    wire, field, value, later
):
    if later:
        wire.reply(payload=page(row(), next_uri=NEXT))
    wire.reply(payload=page(row(**{field: value})))
    with pytest.raises(ValueError):
        fetch()
    assert len(wire.sent) == 1 + later


@pytest.mark.req("VR-006")
@pytest.mark.parametrize("field", ["sid", "status", "direction"])
def test_missing_required_row_fields_raise(wire, field):
    payload = row()
    del payload[field]
    wire.reply(payload=page(payload))
    with pytest.raises(ValueError):
        fetch()


@pytest.mark.req("VR-006")
@pytest.mark.parametrize("absolute", [False, True])
@pytest.mark.parametrize("host", HOSTS)
def test_two_pages_preserve_query_and_request_each_once(
    wire, monkeypatch, host, absolute
):
    monkeypatch.setenv("TWILIO_HTTP_TIMEOUT", "2.5")
    target = (f"https://{host}" if absolute else "") + NEXT
    wire.reply(payload=page(row(), next_uri=target))
    wire.reply(payload=page(row(sid="CAsecond")))
    assert [r.call_sid for r in fetch(api_host=host, max_pages=2)] == [
        "CA123",
        "CAsecond",
    ]
    assert len(wire.sent) == 2
    assert wire.sent[1].request.url == f"https://{host}{NEXT}"
    assert "StartTime" not in wire.sent[1].request.url
    assert all(s.timeout == 2.5 and s.allow_redirects is False for s in wire.sent)
    assert all(
        s.request.headers["Authorization"]
        == wire.sent[0].request.headers["Authorization"]
        for s in wire.sent
    )


@pytest.mark.req("VR-006")
def test_complete_empty_is_success(wire):
    wire.reply(payload=page())
    assert fetch() == []
    assert len(wire.sent) == 1


@pytest.mark.req("VR-006")
@pytest.mark.parametrize("cap", [1, 2, 10])
@pytest.mark.parametrize("empty", [False, True])
def test_cap_with_continuation_is_incomplete_even_if_empty(wire, cap, empty):
    for _ in range(cap):
        wire.reply(payload=page(*([] if empty else [row()]), next_uri=NEXT))
    with pytest.raises(TwilioCallListIncompleteError) as caught:
        fetch(max_pages=cap)
    assert caught.value.api_host == HOSTS[0] and caught.value.pages_fetched == cap
    assert len(wire.sent) == cap
    assert "secret" not in str(caught.value)


@pytest.mark.req("VR-006")
@pytest.mark.parametrize(
    "link",
    [
        "https://evil.invalid" + NEXT,
        "//evil.invalid" + NEXT,
        "http://api.twilio.com" + NEXT,
        "https://api.twilio.com:443" + NEXT,
        "https://user@api.twilio.com" + NEXT,
        NEXT.replace("AC123", "ACother"),
        NEXT.replace("Calls.json", "Messages.json"),
        NEXT + "#fragment",
        NEXT + "#",
        "https://[bad",
        " " + NEXT,
        NEXT + "\n",
        42,
        [],
        {},
        "",
        "https:///" + NEXT,
        "https:evil",
        "ftp://api.twilio.com" + NEXT,
        COLLECTION.replace("AC123", "AC%31%32%33"),
        COLLECTION.replace("Calls.json", "x/../Calls.json"),
        COLLECTION + "?PageToken=%zz",
        COLLECTION + "?PageToken=abc%",
        "\\evil.invalid" + NEXT,
    ],
)
def test_unsafe_continuation_rejected_before_any_second_request(wire, link):
    wire.reply(payload=page(row(), next_uri=link))
    with pytest.raises(ValueError):
        fetch()
    assert len(wire.sent) == 1


@pytest.mark.req("VR-006")
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"next_page_uri": None},
        {"calls": []},
        {"calls": {}, "next_page_uri": None},
        {"calls": None, "next_page_uri": None},
        {"calls": "", "next_page_uri": None},
        {"calls": [None], "next_page_uri": None},
        [],
        None,
    ],
)
@pytest.mark.parametrize("later", [False, True])
def test_malformed_page_cannot_become_empty_success(wire, payload, later):
    if later:
        wire.reply(payload=page(row(), next_uri=NEXT))
    wire.reply(payload=payload)
    with pytest.raises((ValueError, TypeError, TwilioException, AttributeError)):
        fetch()
    assert len(wire.sent) == 1 + later


@pytest.mark.req("VR-006")
@pytest.mark.parametrize("failure", ["http", "transport", "json", "redirect"])
@pytest.mark.parametrize("later", [False, True])
def test_page_failure_propagates_and_never_follows_redirect(wire, failure, later):
    if later:
        wire.reply(payload=page(row(), next_uri=NEXT))
    if failure == "transport":
        wire.replies.append(ReadTimeout("bounded"))
    elif failure == "json":
        wire.reply(text="not json")
    else:
        wire.reply(
            302 if failure == "redirect" else 500,
            headers={"Location": "https://evil.invalid"},
        )
    with pytest.raises((ReadTimeout, ValueError, TwilioException)):
        fetch()
    assert len(wire.sent) == 1 + later
    assert all(s.timeout == 15 and not s.allow_redirects for s in wire.sent)


@pytest.mark.req("VR-006")
@pytest.mark.parametrize("timeout", ["bad", "nan", "inf", "-inf", "0", "-1"])
def test_bad_timeout_never_reaches_http(wire, monkeypatch, timeout):
    monkeypatch.setenv("TWILIO_HTTP_TIMEOUT", timeout)
    with pytest.raises(ValueError):
        fetch()
    assert wire.sent == []


@pytest.mark.req("VR-006")
def test_legacy_read_contract_preserved_with_deprecation_only(wire, monkeypatch):
    from voice_runtime.transports import twilio_call

    assert list_recent_calls() == []
    assert wire.constructed == 0
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "token")
    monkeypatch.delenv("TWILIO_PHONE_NUMBER", raising=False)
    client = MagicMock()
    item = MagicMock(sid="CA123", status=None, start_time=None)
    client.calls.list.return_value = [item]
    monkeypatch.setattr(twilio_call, "build_twilio_client", lambda *args: client)
    assert list_recent_calls() == [{"call_sid": "CA123", "status": "", "start_time": 0}]
    assert client.calls.list.call_args.kwargs["limit"] == 200
    assert client.calls.list.call_args.kwargs["to"] is None
    assert list(inspect.signature(list_recent_calls).parameters) == ["lookback_s"]
    assert (
        inspect.signature(list_recent_calls).parameters["lookback_s"].default == 3600.0
    )
    assert "deprecated" in list_recent_calls.__doc__.lower()
