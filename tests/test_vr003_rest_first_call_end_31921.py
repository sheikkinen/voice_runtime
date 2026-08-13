"""VR-003 RED: REST-first call end — stop emitting Twilio 31921 on every call.

In bidirectional <Connect><Stream>, ANY server-side WS close — including a
clean close(1000) — is logged by Twilio as error 31921. Bot-initiated
disconnects must therefore end the call at the REST boundary first
(hangup_call → Twilio closes the media WS from ITS side) and fall back to
the server-side close only if REST fails or the Twilio-side close never
arrives within the bounded wait.

Deterministic and offline: `hangup_call` is always patched at the
`voice_runtime.transports.twilio_ws` seam (judgement R-2); Twilio's REST
client is mocked for the terminal-predicate tests (R-4).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.websockets import WebSocketDisconnect
from twilio.base.exceptions import TwilioRestException

_CREDS = {
    "TWILIO_ACCOUNT_SID": "AC1",
    "TWILIO_AUTH_TOKEN": "token",
    "TWILIO_SKIP_SIGNATURE_VALIDATION": "1",
}


@pytest.fixture
def creds(monkeypatch):
    for key, value in _CREDS.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def no_creds(monkeypatch):
    monkeypatch.setenv("TWILIO_SKIP_SIGNATURE_VALIDATION", "1")
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)


def _make_session():
    from voice_runtime.session import VoiceSession

    s = VoiceSession()
    s.set_loop(asyncio.get_event_loop())
    return s


def _capture_handler(session):
    """Register the twilio WS route and capture the endpoint closure."""
    from voice_runtime.transports.twilio_ws import register_voice_websocket

    captured = None

    def fake_ws_decorator(path):
        def decorator(fn):
            nonlocal captured
            captured = fn
            return fn

        return decorator

    app = MagicMock()
    app.websocket = fake_ws_decorator
    register_voice_websocket(app, session)
    return captured


def _make_ws(order: list, *, call_sid: str | None = "test-call"):
    """WS mock: plays connected+start, then blocks until Twilio-side close."""
    start: dict = {"event": "start", "streamSid": "test-sid", "start": {}}
    if call_sid is not None:
        start["start"]["callSid"] = call_sid
    messages = [json.dumps({"event": "connected"}), json.dumps(start)]
    idx = 0
    twilio_close = asyncio.Event()

    async def receive_text():
        nonlocal idx
        if idx < len(messages):
            msg = messages[idx]
            idx += 1
            return msg
        await twilio_close.wait()
        raise WebSocketDisconnect(1000)

    async def close(code=1000):
        order.append(("close", code))

    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.receive_text = receive_text
    ws.send_text = AsyncMock()
    ws.send_json = AsyncMock()
    ws.close = AsyncMock(side_effect=close)
    ws._twilio_close = twilio_close
    return ws


async def _poll(predicate, timeout=2.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


async def _finish(task, ws):
    ws._twilio_close.set()
    with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
        await asyncio.wait_for(task, timeout=2.0)


@pytest.mark.req("VR-003")
class TestRestFirstDisconnect:
    """AC-01/AC-02: REST hangup before any server-side close."""

    @pytest.mark.asyncio
    async def test_rest_hangup_before_any_server_close(self, creds, monkeypatch):
        """Disconnect request → hangup_call fires; Twilio closes; NO close(1000)."""
        import voice_runtime.transports.twilio_ws as twilio_ws

        order: list = []
        monkeypatch.setattr(
            twilio_ws,
            "hangup_call",
            lambda sid: order.append(("rest", sid)),
            raising=False,
        )

        session = _make_session()
        handler = _capture_handler(session)
        ws = _make_ws(order)
        task = asyncio.create_task(handler(ws))

        assert await _poll(lambda: session.call_sid == "test-call")
        session.request_disconnect()

        assert await _poll(lambda: ("rest", "test-call") in order), (
            f"REST hangup never attempted on disconnect; order={order}"
        )
        # Twilio closes the WS from its side after the REST hangup
        await _finish(task, ws)

        assert order == [("rest", "test-call")], (
            f"server-side close must not fire when Twilio closes first: {order}"
        )

    @pytest.mark.asyncio
    async def test_rest_failure_falls_back_to_ws_close(self, creds, monkeypatch):
        """AC-03: REST hangup raising → fallback close(1000), call always ends."""
        import voice_runtime.transports.twilio_ws as twilio_ws

        order: list = []

        def failing_hangup(sid):
            order.append(("rest", sid))
            raise RuntimeError("twilio down")

        monkeypatch.setattr(twilio_ws, "hangup_call", failing_hangup, raising=False)

        session = _make_session()
        handler = _capture_handler(session)
        ws = _make_ws(order)
        task = asyncio.create_task(handler(ws))

        assert await _poll(lambda: session.call_sid == "test-call")
        session.request_disconnect()

        assert await _poll(lambda: ("close", 1000) in order), (
            f"fallback WS close never happened after REST failure; order={order}"
        )
        assert order[0] == ("rest", "test-call"), "REST must be attempted first"
        await _finish(task, ws)

    @pytest.mark.asyncio
    async def test_inbound_close_timeout_falls_back_to_ws_close(
        self, creds, monkeypatch
    ):
        """AC-03: REST ok but Twilio never closes → bounded wait then close(1000)."""
        import voice_runtime.transports.twilio_ws as twilio_ws

        order: list = []
        monkeypatch.setattr(
            twilio_ws,
            "hangup_call",
            lambda sid: order.append(("rest", sid)),
            raising=False,
        )
        monkeypatch.setattr(twilio_ws, "REST_CLOSE_WAIT_S", 0.2, raising=False)

        session = _make_session()
        handler = _capture_handler(session)
        ws = _make_ws(order)
        task = asyncio.create_task(handler(ws))

        assert await _poll(lambda: session.call_sid == "test-call")
        session.request_disconnect()

        assert await _poll(lambda: ("close", 1000) in order), (
            f"fallback close never fired after inbound-close timeout; order={order}"
        )
        assert order == [("rest", "test-call"), ("close", 1000)]
        await _finish(task, ws)


@pytest.mark.req("VR-003")
class TestLegacyPath:
    """AC-05: missing credentials or call SID → current behavior, zero REST."""

    @pytest.mark.asyncio
    async def test_no_creds_keeps_legacy_close(self, no_creds, monkeypatch):
        import voice_runtime.transports.twilio_ws as twilio_ws

        order: list = []
        monkeypatch.setattr(
            twilio_ws,
            "hangup_call",
            lambda sid: order.append(("rest", sid)),
            raising=False,
        )

        session = _make_session()
        handler = _capture_handler(session)
        ws = _make_ws(order)
        task = asyncio.create_task(handler(ws))

        assert await _poll(lambda: session.call_sid == "test-call")
        session.request_disconnect()

        assert await _poll(lambda: ("close", 1000) in order)
        assert ("rest", "test-call") not in order, "no REST attempt without creds"
        await _finish(task, ws)

    @pytest.mark.asyncio
    async def test_no_call_sid_keeps_legacy_close(self, creds, monkeypatch):
        import voice_runtime.transports.twilio_ws as twilio_ws

        order: list = []
        monkeypatch.setattr(
            twilio_ws,
            "hangup_call",
            lambda sid: order.append(("rest", sid)),
            raising=False,
        )

        session = _make_session()
        handler = _capture_handler(session)
        ws = _make_ws(order, call_sid=None)
        task = asyncio.create_task(handler(ws))

        assert await _poll(lambda: session.stream_sid == "test-sid")
        session.request_disconnect()

        assert await _poll(lambda: ("close", 1000) in order)
        assert not [e for e in order if e[0] == "rest"], "no REST without call SID"
        await _finish(task, ws)


@pytest.mark.req("VR-003")
class TestTerminalPredicate:
    """AC-04: exact idempotent-success predicate on hangup_call (R-4)."""

    def _hangup_raising(self, exc):
        from voice_runtime.transports.twilio_call import hangup_call

        mock_client = MagicMock()
        mock_client.calls.return_value.update.side_effect = exc
        with (
            patch.dict("os.environ", _CREDS, clear=False),
            patch(
                "voice_runtime.transports.twilio_call.build_twilio_client",
                return_value=mock_client,
            ),
        ):
            hangup_call("CA123")

    def test_404_is_idempotent_success(self):
        self._hangup_raising(TwilioRestException(404, "/Calls/CA123", "not found"))

    def test_400_code_21220_is_idempotent_success(self):
        self._hangup_raising(
            TwilioRestException(400, "/Calls/CA123", "not in-progress", 21220)
        )

    def test_other_errors_still_propagate(self):
        with pytest.raises(TwilioRestException):
            self._hangup_raising(
                TwilioRestException(500, "/Calls/CA123", "server error")
            )

    def test_400_other_code_still_propagates(self):
        with pytest.raises(TwilioRestException):
            self._hangup_raising(
                TwilioRestException(400, "/Calls/CA123", "bad request", 20001)
            )


@pytest.mark.req("VR-003")
class TestOffLoopExecution:
    """AC-06: blocking REST hangup must not stall the media event loop."""

    @pytest.mark.asyncio
    async def test_event_loop_ticks_while_hangup_blocks(self, creds, monkeypatch):
        import voice_runtime.transports.twilio_ws as twilio_ws

        order: list = []
        ticks = 0
        tick_deltas: list[int] = []

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        def blocking_hangup(sid):
            entry = ticks
            time.sleep(0.3)
            tick_deltas.append(ticks - entry)
            order.append(("rest", sid))

        monkeypatch.setattr(twilio_ws, "hangup_call", blocking_hangup, raising=False)

        session = _make_session()
        handler = _capture_handler(session)
        ws = _make_ws(order)
        tick_task = asyncio.create_task(ticker())
        task = asyncio.create_task(handler(ws))

        assert await _poll(lambda: session.call_sid == "test-call")
        session.request_disconnect()

        assert await _poll(lambda: bool(tick_deltas), timeout=3.0), (
            "REST hangup never ran"
        )
        await _finish(task, ws)
        tick_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tick_task

        assert tick_deltas[0] >= 10, (
            f"event loop stalled during blocking hangup: only {tick_deltas[0]} "
            "ticks in 0.3s — REST hangup must run off-loop (asyncio.to_thread)"
        )
