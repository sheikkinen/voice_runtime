"""Twilio Media Streams WebSocket handler.

NC-152: Merged from ninchat_voice/services/twilio_ws.py and
outcaller/server_base.py. Provider-agnostic — no ElevenLabs or STT imports.

The /voice WebSocket endpoint implements the Twilio Media Streams protocol:
- Receives "connected", "start", "media", "stop" messages from Twilio
- Sends base64-encoded mulaw audio frames back to Twilio
- Sends mark events for TTS synchronization
- Taps caller audio to session monitoring (optional)
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
from typing import TYPE_CHECKING

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

if TYPE_CHECKING:
    from projects.voice_runtime.session import VoiceSession

logger = logging.getLogger(__name__)


def register_voice_websocket(app: FastAPI, session: VoiceSession) -> None:
    """Register the Twilio Media Streams WebSocket handler.

    Args:
        app: FastAPI application to register the route on.
        session: VoiceSession to use for audio queues and call state.
    """

    @app.websocket("/voice")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        """Handle Twilio Media Streams WebSocket connection."""
        await websocket.accept()
        logger.info("WebSocket connection accepted")

        async def send_audio() -> None:
            while True:
                try:
                    audio_data = await session.get_outbound()
                    payload = {
                        "event": "media",
                        "streamSid": session.stream_sid,
                        "media": {"payload": base64.b64encode(audio_data).decode()},
                    }
                    await websocket.send_text(json.dumps(payload))
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error("Error sending audio: %s", e)
                    break

        async def send_marks() -> None:
            while True:
                try:
                    mark_name = await session.get_pending_mark()
                    mark_payload = {
                        "event": "mark",
                        "streamSid": session.stream_sid,
                        "mark": {"name": mark_name},
                    }
                    await websocket.send_text(json.dumps(mark_payload))
                    logger.info("Sent mark: %s", mark_name)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error("Error sending mark: %s", e)
                    break

        async def watch_disconnect() -> None:
            """NC-154: Watch for session.request_disconnect() and close WebSocket."""
            if session._disconnect_requested is None:
                return
            await session._disconnect_requested.wait()
            logger.info("Disconnect requested — closing WebSocket to end call")
            with contextlib.suppress(Exception):
                await websocket.close(1000)

        async def send_clears() -> None:
            """NC-154: Watch for session.request_clear_buffer() and send Twilio clear."""
            if session._clear_queue is None:
                return
            while True:
                try:
                    sid = await session._clear_queue.get()
                    await websocket.send_json({"event": "clear", "streamSid": sid})
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.error("Error sending clear: %s", exc)
                    break

        send_task: asyncio.Task | None = None
        mark_task: asyncio.Task | None = None
        disconnect_task: asyncio.Task | None = None  # NC-154
        clear_task: asyncio.Task | None = None       # NC-154
        stt_task: asyncio.Task | None = None         # NC-154

        try:
            while True:
                message = await websocket.receive_text()
                data = json.loads(message)
                event = data.get("event")

                if event == "connected":
                    logger.info("Twilio connected")

                elif event == "start":
                    stream_sid = data.get("streamSid")
                    call_sid = data.get("start", {}).get("callSid")
                    logger.info(
                        "Stream started: stream_sid=%s, call_sid=%s",
                        stream_sid,
                        call_sid,
                    )
                    session.call_sid = call_sid
                    session.signal_ws_connected(stream_sid)

                    send_task = asyncio.create_task(send_audio())
                    mark_task = asyncio.create_task(send_marks())
                    disconnect_task = asyncio.create_task(watch_disconnect())
                    clear_task = asyncio.create_task(send_clears())

                    # NC-154: STT lifecycle — create via factory if provided
                    if session.stt_factory is not None and session.stt is None:
                        session.stt = session.stt_factory()
                    if session.stt is not None:
                        stt_task = asyncio.create_task(
                            session.stt.start(session.inbound), name="stt_start"
                        )

                elif event == "media":
                    payload = data.get("media", {}).get("payload", "")
                    if payload:
                        audio_bytes = base64.b64decode(payload)
                        session.put_inbound(audio_bytes)
                        session.tap_caller(audio_bytes)

                elif event == "mark":
                    mark_name = data.get("mark", {}).get("name", "")
                    logger.info("Received mark: %s", mark_name)
                    session.signal_mark_received(mark_name)

                elif event == "stop":
                    logger.info("Stream stopped - user disconnected")
                    session.signal_disconnected()
                    break

        except WebSocketDisconnect:
            logger.info("WebSocket disconnected")
            session.signal_disconnected()
        except Exception as e:
            if "not connected" in str(e).lower():
                logger.info("WebSocket closed (server-initiated disconnect)")
            else:
                logger.error("WebSocket error: %s", e)
            session.signal_disconnected()
        finally:
            # NC-154: stop STT gracefully before cancelling tasks
            if session.stt is not None:
                with contextlib.suppress(Exception):
                    await session.stt.stop()
            if stt_task is not None:
                stt_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await stt_task
            if clear_task is not None:
                clear_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await clear_task
            if disconnect_task is not None:
                disconnect_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await disconnect_task
            if send_task is not None:
                send_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await send_task
            if mark_task is not None:
                mark_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await mark_task
