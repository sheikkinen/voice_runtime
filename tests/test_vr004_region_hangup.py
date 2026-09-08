"""VR-004: real SDK routing/auth/error semantics, with no network."""

import asyncio
import base64
import os
from urllib.parse import urlsplit

import pytest
from requests.exceptions import ReadTimeout
from twilio.base.exceptions import TwilioRestException

from tests.twilio_http_fakes import FakeTwilioHTTP
from voice_runtime.transports.twilio_call import (
    TwilioCallNotFoundError,
    TwilioRegion,
    hangup_call,
)

HOSTS = ("api.twilio.com", "api.dublin.ie1.twilio.com")
BAD_SIDS = (
    "",
    " ",
    "AC/1",
    "AC\\1",
    "AC?1",
    "AC#1",
    "AC%2f1",
    "AC 1",
    "AC\t1",
    "AC\n1",
    "AC\x001",
    "ÅC1",
    "AC-1",
    12,
    None,
)
BAD_HOSTS = (
    "https://api.twilio.com",
    "api.twilio.com:443",
    "u@api.twilio.com",
    "api.twilio.com/a",
    "api.twilio.com?q",
    "api.twilio.com#x",
    "API.twilio.com",
    "api.ie1.twilio.com",
    "api.x.ie1.evil.com",
    "api.-x.ie1.twilio.com",
    "api.x_.ie1.twilio.com",
    "api.x.ie1.twilio.com.",
)


@pytest.fixture
def wire(monkeypatch):
    return FakeTwilioHTTP(monkeypatch)


def regions():
    return tuple(
        TwilioRegion(api_host=h, account_sid="AC123", auth=(f"SK{i}", f"secret{i}"))
        for i, h in enumerate(HOSTS)
    )


@pytest.mark.req("VR-004")
@pytest.mark.parametrize("second", [False, True])
@pytest.mark.parametrize("terminal", [200, 204, 400])
def test_order_auth_final_urls_and_positive_stop(wire, monkeypatch, second, terminal):
    for key, value in {
        "TWILIO_ACCOUNT_SID": "ambient",
        "TWILIO_AUTH_TOKEN": "ambient",
        "TWILIO_REGION": "au1",
        "TWILIO_EDGE": "sydney",
    }.items():
        monkeypatch.setenv(key, value)
    before = dict(os.environ)
    if second:
        wire.reply(404, {"code": 20404})
    wire.reply(terminal, {"code": 21220} if terminal == 400 else {})
    assert hangup_call("CA123", regions=regions()) is None
    assert len(wire.sent) == 1 + second
    for i, sent in enumerate(wire.sent):
        assert (
            sent.request.url
            == f"https://{HOSTS[i]}/2010-04-01/Accounts/AC123/Calls/CA123.json"
        )
        assert sent.request.method == "POST"
        assert sent.request.body == "Status=completed"
        assert (
            sent.request.headers["Authorization"]
            == "Basic " + base64.b64encode(f"SK{i}:secret{i}".encode()).decode()
        )
        assert sent.timeout == 15.0
        assert sent.allow_redirects is False
    assert dict(os.environ) == before


@pytest.mark.req("VR-004")
def test_api_keys_without_token_and_exhaustion(wire, caplog):
    for _ in HOSTS:
        wire.reply(404, {"code": 20404})
    with pytest.raises(TwilioCallNotFoundError) as caught:
        hangup_call("CA123", regions=regions())
    assert caught.value.attempted_hosts == HOSTS
    assert len(wire.sent) == 2
    assert all(h in str(caught.value) for h in HOSTS)
    assert "secret" not in str(caught.value)
    assert "already terminal" not in caplog.text


@pytest.mark.req("VR-004")
@pytest.mark.parametrize(
    "status,code",
    [
        (401, 20003),
        (403, 20003),
        (429, 20429),
        (500, 1),
        (400, 20001),
        (404, 1),
        (302, 1),
    ],
)
def test_other_errors_stop_immediately(wire, status, code):
    wire.reply(
        status, {"code": code}, headers={"Location": "https://elsewhere.invalid"}
    )
    with pytest.raises(TwilioRestException):
        hangup_call("CA123", regions=regions())
    assert len(wire.sent) == 1


@pytest.mark.req("VR-004")
def test_timeout_propagates_without_retry(wire):
    error = ReadTimeout("bounded")
    wire.replies.append(error)
    with pytest.raises(ReadTimeout) as caught:
        hangup_call("CA123", regions=regions())
    assert caught.value is error
    assert len(wire.sent) == 1


@pytest.mark.req("VR-004")
@pytest.mark.parametrize("sid", BAD_SIDS)
@pytest.mark.parametrize("field", ["call", "account"])
def test_unsafe_sids_rejected_before_construction(wire, field, sid):
    with pytest.raises(ValueError):
        config = TwilioRegion(
            api_host=HOSTS[0],
            account_sid=sid if field == "account" else "AC123",
            auth=("key", "secret"),
        )
        hangup_call(sid if field == "call" else "CA123", regions=(config,))
    assert wire.constructed == 0
    assert wire.sent == []


@pytest.mark.req("VR-004")
@pytest.mark.parametrize("host", BAD_HOSTS)
def test_invalid_hosts_rejected_before_construction(wire, host):
    with pytest.raises(ValueError):
        hangup_call(
            "CA123",
            regions=(
                TwilioRegion(
                    api_host=host, account_sid="AC123", auth=("key", "secret")
                ),
            ),
        )
    assert wire.constructed == 0


@pytest.mark.req("VR-004")
@pytest.mark.parametrize(
    "auth",
    [
        ("key", ""),
        (" ", "secret"),
        ("key",),
        ("key", "secret", "extra"),
        (12, "secret"),
        ["key", "secret"],
        None,
    ],
)
def test_invalid_auth_hidden_from_diagnostics(wire, auth):
    with pytest.raises(ValueError) as caught:
        hangup_call(
            "CA123",
            regions=(TwilioRegion(api_host=HOSTS[0], account_sid="AC123", auth=auth),),
        )
    assert "secret" not in str(caught.value)
    assert "key'" not in str(caught.value)
    assert wire.constructed == 0


@pytest.mark.req("VR-004")
def test_region_is_frozen_and_auth_hidden(wire):
    region = regions()[0]
    assert "SK0" not in repr(region) and "secret0" not in repr(region)
    with pytest.raises(ValueError):
        region.account_sid = "ACother"


@pytest.mark.req("VR-004")
@pytest.mark.parametrize(
    "which", ["empty", "duplicate", "list", "invalid-later", "constructed-invalid"]
)
def test_entire_region_tuple_validated_before_construction(wire, which):
    r = regions()[0]
    candidates = {
        "empty": (),
        "duplicate": (r, r),
        "list": [r],
        "invalid-later": (r, object()),
        "constructed-invalid": (
            r,
            TwilioRegion.model_construct(
                api_host=HOSTS[1], account_sid="AC/1", auth=("key", "secret")
            ),
        ),
    }
    with pytest.raises(ValueError):
        hangup_call("CA123", regions=candidates[which])
    assert wire.constructed == 0


@pytest.mark.req("VR-004")
@pytest.mark.parametrize("timeout", ["abc", "nan", "inf", "-inf", "0", "-1"])
def test_bad_timeout_fails_before_http(wire, monkeypatch, timeout):
    monkeypatch.setenv("TWILIO_HTTP_TIMEOUT", timeout)
    with pytest.raises(ValueError):
        hangup_call("CA123", regions=regions())
    assert wire.sent == []


@pytest.mark.req("VR-004")
def test_override_timeout_on_every_attempt(wire, monkeypatch):
    monkeypatch.setenv("TWILIO_HTTP_TIMEOUT", "2.5")
    wire.reply(404, {"code": 20404})
    wire.reply()
    hangup_call("CA123", regions=regions())
    assert [r.timeout for r in wire.sent] == [2.5, 2.5]


@pytest.mark.req("VR-004")
def test_legacy_absence_reports_actual_ambient_host(wire, monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setenv("TWILIO_EDGE", "sydney")
    monkeypatch.setenv("TWILIO_REGION", "au1")
    wire.reply(404, {"code": 20404})
    with pytest.raises(TwilioCallNotFoundError) as caught:
        hangup_call("CA123")
    assert caught.value.attempted_hosts == (
        urlsplit(wire.sent[0].request.url).hostname,
    )
    assert caught.value.attempted_hosts == ("api.sydney.au1.twilio.com",)


@pytest.mark.req("VR-004")
@pytest.mark.asyncio
async def test_real_legacy_404_reaches_ws_close_without_success_wait(wire, monkeypatch):
    from tests.test_vr003_rest_first_call_end_31921 import (
        _make_session,
        _capture_handler,
        _make_ws,
        _poll,
        _finish,
    )

    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setenv("TWILIO_SKIP_SIGNATURE_VALIDATION", "1")
    wire.reply(404, {"code": 20404})
    session = _make_session()
    order = []
    ws = _make_ws(order, call_sid="CA123")
    task = asyncio.create_task(_capture_handler(session)(ws))
    try:
        assert await _poll(lambda: session.call_sid == "CA123")
        session.request_disconnect()
        assert await _poll(lambda: ("close", 1000) in order, timeout=1)
        assert len(wire.sent) == 1
    finally:
        await _finish(task, ws)
