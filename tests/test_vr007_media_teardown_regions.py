"""VR-007: region-aware REST hangup at media teardown.

The media route accepts the consumer's validated `TwilioRegion` tuple at
registration and selects VR-004's explicit-region hangup inside VR-003's
REST-first teardown. Offline: `hangup_call` is patched at the
`voice_runtime.transports.twilio_ws` seam, as in the VR-003 suite.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from unittest.mock import MagicMock

import pytest
from requests.exceptions import ReadTimeout
from twilio.base.exceptions import TwilioRestException
from voice_runtime.transports.twilio_call import (
    TwilioCallNotFoundError,
    TwilioRegion,
)

from tests.test_vr003_rest_first_call_end_31921 import (
    _finish,
    _make_session,
    _make_ws,
    _poll,
)

HOSTS = ("api.twilio.com", "api.dublin.ie1.twilio.com")
SID = "CA123"


def _regions() -> tuple[TwilioRegion, ...]:
    return tuple(
        TwilioRegion(api_host=h, account_sid="AC123", auth=(f"SK{i}", f"secret{i}"))
        for i, h in enumerate(HOSTS)
    )


def _register(session, **kwargs):
    """Register the route; return (app mock, captured endpoint)."""
    from voice_runtime.transports.twilio_ws import register_voice_websocket

    assert "hangup_regions" in inspect.signature(register_voice_websocket).parameters
    captured = []

    def fake_ws_decorator(path):
        def decorator(fn):
            captured.append(fn)
            return fn

        return decorator

    app = MagicMock()
    app.websocket = MagicMock(side_effect=fake_ws_decorator)
    register_voice_websocket(app, session, **kwargs)
    return app, captured[0]


@pytest.fixture
def no_ambient(monkeypatch):
    monkeypatch.setenv("TWILIO_SKIP_SIGNATURE_VALIDATION", "1")
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)


@pytest.fixture
def ambient(monkeypatch):
    monkeypatch.setenv("TWILIO_SKIP_SIGNATURE_VALIDATION", "1")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "token")


@pytest.fixture
def rest(monkeypatch):
    """Record every hangup_call at the twilio_ws seam: (sid, kwargs, thread)."""
    import voice_runtime.transports.twilio_ws as twilio_ws

    calls: list = []
    behaviour: dict = {"raise": None}

    def fake_hangup(sid, **kwargs):
        calls.append((sid, kwargs, threading.get_ident()))
        if behaviour["raise"] is not None:
            raise behaviour["raise"]

    monkeypatch.setattr(twilio_ws, "hangup_call", fake_hangup)
    return calls, behaviour


async def _drive(order, **kwargs):
    """Register, play connected+start, request disconnect; return (task, ws)."""
    session = _make_session()
    _app, handler = _register(session, **kwargs)
    ws = _make_ws(order, call_sid=SID)
    task = asyncio.create_task(handler(ws))
    assert await _poll(lambda: session.call_sid == SID)
    session.request_disconnect()
    return task, ws


@pytest.mark.req("VR-007")
class TestRegistrationValidation:
    """AC-02: one validator, run synchronously at registration."""

    @pytest.mark.parametrize(
        "which", ["empty", "list", "duplicate", "invalid-later", "constructed-invalid"]
    )
    def test_bad_tuple_rejected_before_route_use(self, no_ambient, which):
        r = _regions()[0]
        candidates = {
            "empty": (),
            "list": [r],
            "duplicate": (r, r),
            "invalid-later": (r, object()),
            "constructed-invalid": (
                r,
                TwilioRegion.model_construct(
                    api_host=HOSTS[1], account_sid="AC/1", auth=("SKx", "secretx")
                ),
            ),
        }
        from voice_runtime.transports.twilio_ws import register_voice_websocket

        assert (
            "hangup_regions" in inspect.signature(register_voice_websocket).parameters
        )
        app = MagicMock()
        with pytest.raises(ValueError) as caught:
            register_voice_websocket(
                app, _make_session(), hangup_regions=candidates[which]
            )
        assert "secret" not in str(caught.value)
        app.websocket.assert_not_called()

    def test_registration_and_hangup_call_share_one_validator(
        self, no_ambient, monkeypatch
    ):
        """C-3: both entry points consume the shared helper's returned tuple."""
        import voice_runtime.transports.twilio_call as twilio_call

        assert hasattr(twilio_call, "_validated_regions"), "shared helper missing"
        real = twilio_call._validated_regions
        seen: list = []

        def spy(regions):
            seen.append(regions)
            return real(regions)

        monkeypatch.setattr(twilio_call, "_validated_regions", spy)
        _register(_make_session(), hangup_regions=_regions())
        assert len(seen) == 1
        with pytest.raises(ValueError):
            twilio_call.hangup_call(SID, regions=())
        assert len(seen) == 2


@pytest.mark.req("VR-007")
class TestExplicitRegionTeardown:
    """AC-03 / AC-04 / AC-05: explicit regions at media teardown."""

    @pytest.mark.asyncio
    async def test_regions_without_ambient_creds_rest_first(self, no_ambient, rest):
        calls, _ = rest
        order: list = []
        task, ws = await _drive(order, hangup_regions=_regions())
        assert await _poll(lambda: calls), "REST hangup never attempted"
        await _finish(task, ws)

        assert len(calls) == 1
        sid, kwargs, thread = calls[0]
        assert sid == SID
        assert kwargs == {"regions": _regions()}
        assert thread != threading.get_ident(), "hangup must run off the loop"
        assert order == [], f"no server-side close when Twilio closes: {order}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error",
        [
            TwilioCallNotFoundError(HOSTS),
            TwilioRestException(401, "/Calls/CA123", "auth", 20003),
            ReadTimeout("bounded"),
        ],
        ids=["not-found", "401", "timeout"],
    )
    async def test_rest_failure_falls_back_once(self, no_ambient, rest, caplog, error):
        calls, behaviour = rest
        behaviour["raise"] = error
        order: list = []
        task, ws = await _drive(order, hangup_regions=_regions())
        assert await _poll(lambda: ("close", 1000) in order)
        await _finish(task, ws)

        assert order == [("close", 1000)]
        assert len(calls) == 1, "no retry"
        assert "secret" not in caplog.text

    @pytest.mark.asyncio
    async def test_no_twilio_close_bounded_wait_then_close(
        self, no_ambient, rest, monkeypatch
    ):
        import voice_runtime.transports.twilio_ws as twilio_ws

        monkeypatch.setattr(twilio_ws, "REST_CLOSE_WAIT_S", 0.2)
        calls, _ = rest
        order: list = []
        task, ws = await _drive(order, hangup_regions=_regions())
        assert await _poll(lambda: ("close", 1000) in order)
        await _finish(task, ws)

        assert len(calls) == 1
        assert order == [("close", 1000)]


@pytest.mark.req("VR-007")
class TestAmbientModePreserved:
    """AC-06: hangup_regions=None keeps VR-003 ambient semantics."""

    @pytest.mark.asyncio
    async def test_missing_ambient_creds_zero_rest_one_close(self, no_ambient, rest):
        calls, _ = rest
        order: list = []
        task, ws = await _drive(order, hangup_regions=None)
        assert await _poll(lambda: ("close", 1000) in order)
        await _finish(task, ws)

        assert calls == []
        assert order == [("close", 1000)]

    @pytest.mark.asyncio
    async def test_ambient_success_no_regions_no_server_close(self, ambient, rest):
        calls, _ = rest
        order: list = []
        task, ws = await _drive(order, hangup_regions=None)
        assert await _poll(lambda: calls)
        await _finish(task, ws)

        assert [(sid, kwargs) for sid, kwargs, _t in calls] == [(SID, {})]
        assert order == []

    @pytest.mark.asyncio
    async def test_ambient_failure_one_fallback_close(self, ambient, rest):
        calls, behaviour = rest
        behaviour["raise"] = RuntimeError("twilio down")
        order: list = []
        task, ws = await _drive(order, hangup_regions=None)
        assert await _poll(lambda: ("close", 1000) in order)
        await _finish(task, ws)

        assert len(calls) == 1
        assert order == [("close", 1000)]


@pytest.mark.req("VR-007")
class TestSignatureBoundaryUnchanged:
    """AC-08: regions never replace the upgrade's auth-token signature check."""

    @pytest.mark.asyncio
    async def test_bad_signature_rejected_with_regions(self, monkeypatch, rest):
        calls, _ = rest
        monkeypatch.delenv("TWILIO_SKIP_SIGNATURE_VALIDATION", raising=False)
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", "token")
        monkeypatch.setenv("VOICE_STREAM_URL", "https://example.test")
        order: list = []
        _app, handler = _register(_make_session(), hangup_regions=_regions())
        ws = _make_ws(order, call_sid=SID)
        ws.headers = {"x-twilio-signature": "forged"}
        await asyncio.wait_for(handler(ws), timeout=2.0)

        assert order == [("close", 1008)]
        assert calls == []
