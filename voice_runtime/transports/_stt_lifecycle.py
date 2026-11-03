"""Shared STT start for WebSocket transports (VR-011 D-2).

Moved unchanged from the Twilio ``start`` handler so the AudioHook server
transport starts STT with the same lifecycle and call order.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voice_runtime.session import VoiceSession


def start_session_stt(session: VoiceSession) -> asyncio.Task | None:
    """Create STT via the session factories (if needed) and start it.

    Returns the ``stt.start`` task, or None when the session has no STT.
    """
    # NC-154: STT lifecycle — create via factory if provided
    if session.stt_factory is not None and session.stt is None:
        primary_stt = session.stt_factory()
        # NC-164: wrap with SttTee if secondary factory is set
        if session.stt_secondary_factory is not None:
            from voice_runtime.stt_tee import SttTee

            secondary_stt = session.stt_secondary_factory()
            session.stt = SttTee(primary_stt, secondary_stt)
        else:
            session.stt = primary_stt
    if session.stt is None:
        return None
    # NC-260 Gap E: wire callbacks before start
    if session.on_stt_ready is not None:
        session.on_stt_ready(session.stt)
    return asyncio.create_task(session.stt.start(session.inbound), name="stt_start")
