"""RED tests for NC-260 Gap B: Twilio WS send failures must signal disconnect.

When send_audio() or send_marks() hit an exception, the session must be
notified via signal_disconnected() so the FSM can react immediately instead
of waiting for a 30-60s state timeout.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_session():
    """Create a minimal VoiceSession for testing."""
    from voice_runtime.session import VoiceSession

    s = VoiceSession()
    loop = asyncio.get_event_loop()
    s.set_loop(loop)
    s.signal_ws_connected("test-stream-sid")
    return s


def _capture_handler(session):
    """Register twilio WS and capture the inner handler function."""
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


def _make_ws_mock(*, block_after_start=True):
    """Create WS mock that sends connected+start then blocks forever on receive.

    If block_after_start=True, receive_text blocks forever after the start
    message, so the only way session can become disconnected is if an inner
    task (send_audio/send_marks) calls signal_disconnected().
    """
    messages = [
        json.dumps({"event": "connected"}),
        json.dumps({
            "event": "start",
            "streamSid": "test-sid",
            "start": {"callSid": "test-call"},
        }),
    ]
    msg_idx = 0
    block_event = asyncio.Event()

    async def mock_receive_text():
        nonlocal msg_idx
        if msg_idx < len(messages):
            msg = messages[msg_idx]
            msg_idx += 1
            return msg
        if block_after_start:
            # Block forever — only signal_disconnected can unblock via
            # WebSocketDisconnect or similar. We'll cancel from outside.
            await block_event.wait()
            # After unblock, simulate stop
            return json.dumps({"event": "stop"})
        await asyncio.sleep(0.5)
        return json.dumps({"event": "stop"})

    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.receive_text = mock_receive_text
    ws.send_text = AsyncMock(side_effect=ConnectionError("pipe broken"))
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    ws._block_event = block_event
    return ws


@pytest.mark.asyncio
async def test_send_audio_signals_disconnect_on_error():
    """send_audio() must call signal_disconnected() when websocket.send_text raises."""
    session = _make_session()
    handler = _capture_handler(session)

    # Queue audio so send_audio fires immediately after start
    await session.outbound.put(b"\x00" * 160)

    ws = _make_ws_mock(block_after_start=True)

    assert not session.is_disconnected, "precondition: not disconnected"

    # Run handler as a task; it will block on receive_text after start
    task = asyncio.create_task(handler(ws))

    # Poll for disconnect signal (should come from send_audio error path)
    for _ in range(40):  # 40 × 50ms = 2s max
        await asyncio.sleep(0.05)
        if session.is_disconnected:
            break
    else:
        ws._block_event.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        pytest.fail(
            "send_audio did not call signal_disconnected() within 2s after send failure"
        )

    assert session.is_disconnected, (
        "session must be disconnected after send_audio fails"
    )

    # Cleanup: unblock the receive loop and cancel handler
    ws._block_event.set()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_send_marks_signals_disconnect_on_error():
    """send_marks() must call signal_disconnected() when websocket.send_text raises."""
    session = _make_session()
    handler = _capture_handler(session)

    # Queue a mark via the async queue directly (send_mark_and_wait is sync+blocking)
    await session._mark_queue.put("test-mark__abc12345")

    ws = _make_ws_mock(block_after_start=True)

    assert not session.is_disconnected, "precondition: not disconnected"

    task = asyncio.create_task(handler(ws))

    for _ in range(40):  # 40 × 50ms = 2s max
        await asyncio.sleep(0.05)
        if session.is_disconnected:
            break
    else:
        ws._block_event.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        pytest.fail(
            "send_marks did not call signal_disconnected() within 2s after send failure"
        )

    assert session.is_disconnected, (
        "session must be disconnected after send_marks fails"
    )

    ws._block_event.set()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
