"""NC-271: Mock transport bridge — connects two voice processes without Twilio.

Provides:
- FakeWsBridge: WebSocket client that satisfies Twilio Media Streams protocol
  (connected + start events, mark echo) without real telephony.
- initiate_mock_call(): Replaces initiate_outbound_call() for mock mode.
- create_text_relay(): Creates an on_spoken callback that POSTs text to peer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from uuid import uuid4

import httpx

logger = logging.getLogger(__name__)


def create_text_relay(peer_url: str):
    """Create an on_spoken callback that relays text to peer's /test/inject.

    Args:
        peer_url: Base URL of the peer process (e.g. "http://127.0.0.1:8765").

    Returns:
        Callable[[str], None] suitable for MockTts(on_spoken=...).
    """
    client = httpx.Client(timeout=10.0)

    def _relay(text: str) -> None:
        url = f"{peer_url}/test/inject"
        try:
            resp = client.post(url, json={"text": text})
            resp.raise_for_status()
            logger.info("Relayed text to %s (%d chars)", url, len(text))
        except Exception:
            logger.exception("Failed to relay text to %s", url)

    return _relay


class FakeWsBridge:
    """WebSocket client that satisfies Twilio Media Streams protocol.

    Connects to a /voice endpoint, sends connected + start events,
    and echoes marks back. No audio is exchanged.
    """

    def __init__(self, call_sid: str | None = None) -> None:
        self._call_sid = call_sid or f"CAMOCK_{uuid4().hex[:8]}"
        self._stream_sid = f"MZ{uuid4().hex[:8]}"
        self._ws = None
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self, ws_url: str) -> None:
        """Connect to ws_url in a background thread and run the echo loop."""
        self._running = True
        self._thread = threading.Thread(
            target=self._run, args=(ws_url,), daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the bridge to stop."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def _run(self, ws_url: str) -> None:
        """Run the async event loop for the WebSocket connection."""
        asyncio.run(self._async_run(ws_url))

    async def _async_run(self, ws_url: str) -> None:
        """Connect, handshake, and echo marks until stopped."""
        import websockets

        try:
            logger.info(
                "FakeWsBridge connecting: url=%s call_sid=%s",
                ws_url,
                self._call_sid,
            )
            async with websockets.connect(ws_url) as ws:
                self._ws = ws
                # Twilio handshake: connected + start
                await ws.send(json.dumps({"event": "connected"}))
                await ws.send(json.dumps({
                    "event": "start",
                    "streamSid": self._stream_sid,
                    "start": {"callSid": self._call_sid},
                }))
                logger.info(
                    "FakeWsBridge connected: call_sid=%s stream_sid=%s url=%s",
                    self._call_sid,
                    self._stream_sid,
                    ws_url,
                )
                # Echo marks, ignore media, until stopped
                while self._running:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
                    except TimeoutError:
                        continue
                    except websockets.ConnectionClosed as e:
                        logger.info(
                            "FakeWsBridge: peer closed connection "
                            "(code=%s reason=%s)",
                            e.code,
                            e.reason,
                        )
                        break
                    data = json.loads(msg)
                    event = data.get("event")
                    if event == "mark":
                        mark_name = data.get("mark", {}).get("name", "")
                        await ws.send(json.dumps({
                            "event": "mark",
                            "mark": {"name": mark_name},
                        }))
                        logger.info("FakeWsBridge echoed mark: %s", mark_name)
                    elif event == "clear":
                        logger.info("FakeWsBridge received clear")
                    elif event == "media":
                        pass  # ignore audio frames silently
                    else:
                        logger.info(
                            "FakeWsBridge received event: %s", event
                        )
                # Send stop on clean exit
                await ws.send(json.dumps({
                    "event": "stop",
                    "streamSid": self._stream_sid,
                }))
                logger.info("FakeWsBridge sent stop, shutting down")
        except Exception:
            logger.exception("FakeWsBridge error connecting to %s", ws_url)
        finally:
            self._running = False


def initiate_mock_call(target_url: str, caller_url: str | None = None) -> str:
    """Initiate a mock call by POSTing /incoming and connecting FakeWsBridges.

    Replaces initiate_outbound_call() when TRANSPORT=mock.

    Two bridges are created:
    - Target bridge: connects to target's /voice (replaces Twilio→callee)
    - Caller bridge: connects to caller's own /voice (replaces Twilio→caller)

    Both bridges echo marks back, enabling send_mark_and_wait on both sides.

    Args:
        target_url: Base URL of the callee server (e.g. "http://127.0.0.1:8765").
        caller_url: Base URL of the caller's own server. If None, reads
            VOICE_STREAM_URL env var.

    Returns:
        Fake call SID.
    """
    call_sid = f"CAMOCK_{uuid4().hex[:8]}"

    # POST /incoming (replaces Twilio webhook)
    resp = httpx.post(
        f"{target_url}/incoming",
        data={
            "CallSid": call_sid,
            "From": "+358400000000",
        },
        timeout=10.0,
    )
    resp.raise_for_status()
    logger.info("Mock call initiated: call_sid=%s target=%s", call_sid, target_url)

    # Connect FakeWsBridge to target's /voice WebSocket
    target_ws = target_url.replace("https://", "wss://").replace("http://", "ws://")
    target_bridge = FakeWsBridge(call_sid=call_sid)
    target_bridge.start(f"{target_ws}/voice")
    _active_bridges.append(target_bridge)

    # Connect FakeWsBridge to caller's own /voice WebSocket
    # This enables send_mark_and_wait on the caller side
    own_url = caller_url or os.getenv("VOICE_STREAM_URL", "")
    if own_url:
        caller_ws = own_url.replace("https://", "wss://").replace("http://", "ws://")
        caller_bridge = FakeWsBridge(call_sid=call_sid)
        caller_bridge.start(f"{caller_ws}/voice")
        _active_bridges.append(caller_bridge)
    else:
        logger.warning(
            "No caller_url or VOICE_STREAM_URL — caller marks won't echo"
        )

    return call_sid


# Module-level registry for cleanup
_active_bridges: list[FakeWsBridge] = []


def cleanup_bridges() -> None:
    """Stop all active mock bridges."""
    for bridge in _active_bridges:
        bridge.stop()
    _active_bridges.clear()


def is_mock_transport() -> bool:
    """Check if TRANSPORT=mock is set."""
    return os.getenv("TRANSPORT", "").lower() == "mock"
