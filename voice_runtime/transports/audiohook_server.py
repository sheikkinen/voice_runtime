"""AudioHook server transport for Genesys Cloud Audio Connector (VR-011).

EXPERIMENTAL, SANDBOX-ONLY. Genesys is the WebSocket client and this module
is the server. Authentication is the API key plus optional organization
checks; RFC 9421 signatures are logged as present/absent, never verified.

The transport serves the same ``VoiceSession`` contract as ``twilio_ws``:
caller audio is re-chunked to 160-byte frames, bot audio is paced as
1600-byte packets, and marks are emulated from a byte clock because
AudioHook has no wire-level mark. One writer task owns every socket send.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi import WebSocketDisconnect

from voice_runtime.session import FRAME_BYTES
from voice_runtime.transports._audiohook_writer import (
    PACKET_BYTES,
    PACKET_INTERVAL_S,
    POLL_S,
    _Writer,
)
from voice_runtime.transports._stt_lifecycle import start_session_stt

if TYPE_CHECKING:
    from fastapi import FastAPI, WebSocket

    from voice_runtime.session import VoiceSession

__all__ = [
    "CLOSE_WAIT_S",
    "PACKET_BYTES",
    "PACKET_INTERVAL_S",
    "register_audiohook_websocket",
]

logger = logging.getLogger(__name__)

AUTH_FAILURE_DELAY_S = 0.5
CLOSE_WAIT_S = 10.0
_UNAUTHORIZED_INFO = "unauthorized"


def _now() -> float:
    return asyncio.get_running_loop().time()


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def _params(data: dict) -> dict:
    params = data.get("parameters")
    return params if isinstance(params, dict) else {}


def _select_media(offered: Any) -> dict | None:
    """Pick the offered PCMU 8 kHz external-only entry, unchanged."""
    if not isinstance(offered, list):
        return None
    for media in offered:
        if (
            isinstance(media, dict)
            and media.get("type") == "audio"
            and media.get("format") == "PCMU"
            and media.get("rate") == 8000
            and media.get("channels") == ["external"]
        ):
            return media
    return None


async def _cancel(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task


@dataclass
class _Config:
    api_key: str = field(repr=False)
    organization_id: str | None
    mark_latency_s: float


class _Connection:
    """One AudioHook session on one accepted socket (judgement R-2)."""

    def __init__(self, ws: WebSocket, session: VoiceSession, cfg: _Config) -> None:
        self._ws = ws
        self._session = session
        self._cfg = cfg
        self._session_id = ws.headers.get("audiohook-session-id") or ""
        self._client_seq = 0
        self._writer = _Writer(
            ws,
            self._session_id,
            lambda: self._client_seq,
            cfg.mark_latency_s,
            now=lambda: _now(),
            sleep=lambda s: _sleep(s),
        )
        self._accepted_at = 0.0
        self._authenticated = False
        self._opened = False
        self._closed_sent = False
        self._bound = asyncio.Event()
        self._stt_task: asyncio.Task | None = None
        self._remainder = bytearray()

    async def run(self) -> None:
        self._accepted_at = _now()
        writer = asyncio.create_task(self._writer.run(), name="audiohook_writer")
        reader = asyncio.create_task(self._read_loop(), name="audiohook_reader")
        watcher = asyncio.create_task(self._watch_disconnect(), name="audiohook_bye")
        try:
            await asyncio.wait(
                {writer, reader, watcher}, return_when=asyncio.FIRST_COMPLETED
            )
            if reader.done() and not reader.cancelled():
                exc = reader.exception()
                if isinstance(exc, WebSocketDisconnect):
                    logger.info("AudioHook socket disconnected")
                elif exc is not None:
                    logger.error("AudioHook receive failed: %s", exc)
        finally:
            await self._teardown(writer, reader, watcher)

    async def _teardown(self, writer, reader, watcher) -> None:
        await _cancel(reader)
        await _cancel(watcher)
        if self._bound.is_set():
            self._session.signal_disconnected()
            if self._session.stt is not None:
                with contextlib.suppress(Exception):
                    await self._session.stt.stop()
            await _cancel(self._stt_task)
        await _cancel(writer)
        if self._remainder:
            logger.debug("Discarding %d-byte inbound remainder", len(self._remainder))
            self._remainder.clear()
        with contextlib.suppress(Exception):
            await self._ws.close()

    async def _read_loop(self) -> None:
        while True:
            message = await self._ws.receive()
            if message.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect(message.get("code", 1000))
            if message.get("text") is not None:
                if await self._on_text(message["text"]):
                    return
            elif message.get("bytes") is not None:
                self._on_audio(message["bytes"])

    async def _on_text(self, text: str) -> bool:
        """Handle one Genesys text message; True ends the session."""
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if not isinstance(data, dict):
            logger.error("AudioHook malformed text message")
            return await self._fail("malformed message")
        seq = data.get("seq")
        seq_ok = type(seq) is int and seq == self._client_seq + 1
        if seq_ok:
            self._client_seq = seq
        if not self._authenticated:
            if not self._authorized(data):
                await self._reject()
                return True
            self._authenticated = True
        if not seq_ok:
            logger.error(
                "AudioHook sequence fault: expected seq %d, got %r",
                self._client_seq + 1,
                seq,
            )
            return await self._fail("sequence error")
        if not self._opened:
            if data.get("type") != "open":
                logger.error("AudioHook first message was %r", data.get("type"))
                return await self._fail("expected open")
            return await self._on_open(_params(data))
        return await self._dispatch(data)

    def _authorized(self, data: dict) -> bool:
        headers = self._ws.headers
        presented = headers.get("x-api-key") or ""
        ok = hmac.compare_digest(presented.encode(), self._cfg.api_key.encode())
        org = self._cfg.organization_id
        if org is not None:
            ok = ok and headers.get("audiohook-organization-id") == org
            if data.get("type") == "open":
                ok = ok and _params(data).get("organizationId") == org
        return ok

    async def _reject(self) -> None:
        remaining = self._accepted_at + AUTH_FAILURE_DELAY_S - _now()
        if remaining > 0:
            await _sleep(remaining)
        logger.warning("AudioHook session %s rejected: unauthorized", self._session_id)
        await self._writer.send(
            "disconnect", {"reason": "unauthorized", "info": _UNAUTHORIZED_INFO}
        )

    async def _fail(self, info: str) -> bool:
        await self._writer.send("disconnect", {"reason": "error", "info": info})
        return True

    async def _on_open(self, params: dict) -> bool:
        media = _select_media(params.get("media"))
        if media is None:
            logger.error("AudioHook open offered no PCMU 8000 external media")
            return await self._fail("unsupported media")
        if not await self._writer.send(
            "opened", {"startPaused": False, "media": [media]}
        ):
            return True
        self._opened = True
        conversation_id = params.get("conversationId")
        if conversation_id is None:
            logger.info("AudioHook probe session %s opened", self._session_id)
            return False
        session = self._session
        participant = params.get("participant")
        session.call_sid = conversation_id
        session.caller_number = (
            participant.get("ani") if isinstance(participant, dict) else None
        )
        session.signal_ws_connected(self._session_id)
        self._writer.bind(session)
        self._bound.set()
        self._stt_task = start_session_stt(session)
        logger.info("AudioHook call %s bound", conversation_id)
        return False

    async def _dispatch(self, data: dict) -> bool:
        type_ = data.get("type")
        params = _params(data)
        match type_:
            case "ping":
                self._writer.send("pong")
            case "update":
                self._writer.send("updated")
            case "close":
                await self._writer.send("closed")
                self._closed_sent = True
                logger.info("AudioHook close (%s) answered", params.get("reason"))
                return True
            case "error":
                logger.warning(
                    "AudioHook error from Genesys: code=%s retryAfter=%s",
                    params.get("code"),
                    params.get("retryAfter"),
                )
            case "playback_started" | "playback_completed":
                self._writer.log_playback(type_, data.get("position"))
            case "paused" | "resumed" | "discarded" | "dtmf":
                logger.info("AudioHook %s: %s", type_, params)
            case _:
                logger.warning("AudioHook ignoring message type %r", type_)
        return False

    def _on_audio(self, chunk: bytes) -> None:
        if not self._bound.is_set():
            logger.debug("AudioHook dropping %d bytes before bind", len(chunk))
            return
        self._remainder += chunk
        while len(self._remainder) >= FRAME_BYTES:
            frame = bytes(self._remainder[:FRAME_BYTES])
            del self._remainder[:FRAME_BYTES]
            self._session.put_inbound(frame)
            self._session.tap_caller(frame)

    async def _watch_disconnect(self) -> None:
        """Bot ending: disconnect(completed), then wait for Genesys close."""
        await self._bound.wait()
        requested = self._session._disconnect_requested
        if requested is None:  # signal_ws_connected always creates it
            await asyncio.get_running_loop().create_future()
        await requested.wait()
        logger.info("AudioHook bot requested disconnect")
        sent = await self._writer.send(
            "disconnect", {"reason": "completed", "outputVariables": {}}
        )
        if not sent:
            return
        deadline = _now() + CLOSE_WAIT_S
        while not self._closed_sent and _now() < deadline:
            await _sleep(POLL_S)
        if not self._closed_sent:
            logger.warning("AudioHook no close within %.1fs", CLOSE_WAIT_S)


def register_audiohook_websocket(
    app: FastAPI,
    session: VoiceSession,
    *,
    api_key: str,
    organization_id: str | None = None,
    path: str = "/audiohook",
    mark_latency_s: float = 0.2,
) -> None:
    """Register the AudioHook (Genesys Audio Connector) WebSocket route.

    Args:
        app: FastAPI application to register the route on.
        session: VoiceSession serving one call at a time.
        api_key: Expected ``X-API-KEY`` value; supplied by the consumer.
        organization_id: When set, the header and ``open`` org must match.
        path: WebSocket route path.
        mark_latency_s: Delay after a mark's target bytes are sent before
            the mark resolves (byte-clock emulation).
    """
    if not isinstance(api_key, str) or not api_key:
        raise ValueError("api_key must be a non-empty string")
    if (
        isinstance(mark_latency_s, bool)
        or not isinstance(mark_latency_s, int | float)
        or not math.isfinite(mark_latency_s)
        or mark_latency_s < 0
    ):
        raise ValueError("mark_latency_s must be a finite number >= 0")
    cfg = _Config(api_key, organization_id, float(mark_latency_s))

    @app.websocket(path)
    async def audiohook_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        logger.info(
            "AudioHook connection accepted: session=%s signature=%s",
            websocket.headers.get("audiohook-session-id"),
            "present" if websocket.headers.get("signature") else "absent",
        )
        await _Connection(websocket, session, cfg).run()
