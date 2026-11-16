"""Google Cloud Text-to-Speech provider: Chirp 3 HD streaming, native mulaw.

VR-013 §2: streaming_synthesize with MULAW 8 kHz, 160-byte frames, one
non-daemon worker per call and a single 1 s cancellation budget (R-5).
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from google.api_core.client_options import ClientOptions
from google.cloud import texttospeech

from voice_runtime.providers._google_config import (
    DEFAULT_LOCATION,
    env_or,
    resolve_project,
    snake,
)

if TYPE_CHECKING:
    from voice_runtime.session import VoiceSession

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "fi-FI-Chirp3-HD-Charon"
FRAME_BYTES = 160
_POLL_S = 0.05
_CANCEL_BUDGET_S = 1.0
_MULAW_SILENCE = b"\xff"


class _CallControl:
    """Hands the streaming call from the worker to speak() for cancellation.

    api_core prefetches the first response inside streaming_synthesize(), so
    the call object does not exist while the first read is blocked. A cancel
    in that window closes the client transport, which ends every RPC on it.
    """

    def __init__(self, client: Any) -> None:
        self._client = client
        self._lock = threading.Lock()
        self._call: Any = None
        self.cancelled = False

    def attach(self, call: Any) -> bool:
        with self._lock:
            self._call = call
            if self.cancelled:
                call.cancel()
                return False
            return True

    def cancel(self) -> bool:
        """Cancel the call; return True when the transport had to be closed."""
        with self._lock:
            self.cancelled = True
            call = self._call
        if call is not None:
            call.cancel()
            return False
        self._client.transport.close()
        return True


class GoogleTTS:
    """Google Cloud TTS provider using Chirp 3 HD streaming synthesis."""

    def __init__(
        self,
        project: str | None = None,
        location: str | None = None,
        voice_name: str | None = None,
    ) -> None:
        self._project = resolve_project(project, "GoogleTTS")
        self._location = env_or(location, "GOOGLE_SPEECH_LOCATION", DEFAULT_LOCATION)
        self._voice_name = env_or(voice_name, "GOOGLE_TTS_VOICE", DEFAULT_VOICE)
        self._language_code = "-".join(self._voice_name.split("-")[:2])
        self._client: Any = None
        self.on_error: Callable[[str], None] | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = texttospeech.TextToSpeechClient(
                client_options=ClientOptions(
                    api_endpoint=f"{self._location}-texttospeech.googleapis.com",
                    quota_project_id=self._project,
                )
            )
        return self._client

    def _requests(self, text: str) -> list[Any]:
        config = texttospeech.StreamingSynthesizeConfig(
            voice=texttospeech.VoiceSelectionParams(
                name=self._voice_name, language_code=self._language_code
            ),
            streaming_audio_config=texttospeech.StreamingAudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MULAW,
                sample_rate_hertz=8000,
            ),
        )
        return [
            texttospeech.StreamingSynthesizeRequest(streaming_config=config),
            texttospeech.StreamingSynthesizeRequest(
                input=texttospeech.StreamingSynthesisInput(text=text)
            ),
        ]

    @staticmethod
    def _worker(
        client: Any, requests: list[Any], out: queue.Queue, ctl: _CallControl
    ) -> None:
        """Read the response stream into ``out``; never touches the session."""
        try:
            call = client.streaming_synthesize(requests=iter(requests))
            if not ctl.attach(call):
                return
            for response in call:
                if ctl.cancelled:
                    return
                out.put(("audio", response.audio_content))
            out.put(("eof", None))
        # Every worker failure must reach speak() as a message, never vanish.
        except Exception as exc:  # noqa: BLE001 — forwarded to speak(), not swallowed
            out.put(("error", exc))

    @staticmethod
    def _pump(
        out: queue.Queue,
        session: VoiceSession,
        stop_event: threading.Event | None,
        carry: bytearray,
    ) -> tuple[str, Exception | None]:
        """Emit worker audio as frames until EOF, error, stop or disconnect."""
        while True:
            try:
                kind, payload = out.get(timeout=_POLL_S)
            except queue.Empty:
                kind, payload = "idle", None
            if stop_event is not None and stop_event.is_set():
                return "interrupted", None
            if session.is_disconnected:
                return "disconnected", None
            if kind == "audio":
                carry.extend(payload)
                while len(carry) >= FRAME_BYTES:
                    frame = bytes(carry[:FRAME_BYTES])
                    del carry[:FRAME_BYTES]
                    session.put_outbound_sync(frame)
                    session.tap_agent(frame)
            elif kind in ("eof", "error"):
                return kind, payload

    def speak(
        self,
        text: str,
        session: VoiceSession,
        stop_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """Stream TTS audio to the session outbound queue.

        Returns:
            {"last_spoken": text}, optionally with "interrupted",
            "call_disconnected" or "error" as in AzureTTS.
        """
        if not text or not text.strip():
            return {"last_spoken": ""}
        if session.is_disconnected:
            logger.warning("Call disconnected — cannot speak")
            return {"last_spoken": "", "call_disconnected": True}
        if stop_event is not None and stop_event.is_set():
            return {"last_spoken": text, "interrupted": True}
        if not self._project:
            raise ValueError(
                "GoogleTTS: no project (set project, GOOGLE_SPEECH_PROJECT "
                "or GOOGLE_CLOUD_PROJECT)"
            )

        logger.info("Speaking (Google): chars=%d", len(text))
        t0 = time.monotonic()
        client = self._get_client()
        ctl = _CallControl(client)
        out: queue.Queue = queue.Queue()
        worker = threading.Thread(
            target=self._worker,
            args=(client, self._requests(text), out, ctl),
            name="google-tts-worker",
            daemon=False,
        )
        carry = bytearray()
        outcome, error = "aborted", None
        worker.start()
        try:
            outcome, error = self._pump(out, session, stop_event, carry)
        finally:
            deadline = time.monotonic() + _CANCEL_BUDGET_S
            if outcome != "eof" and ctl.cancel():
                self._client = None
            worker.join(timeout=max(0.0, deadline - time.monotonic()))
            if worker.is_alive():
                logger.error("Google TTS worker still alive after cancel budget")

        if outcome == "interrupted":
            logger.info("Barge-in interrupt (Google)")
            return {"last_spoken": text, "interrupted": True}
        if outcome == "disconnected":
            logger.info("Call disconnected during Google TTS")
            return {"last_spoken": text, "call_disconnected": True}
        if outcome == "error":
            logger.error("Google TTS synthesis failed: %s", error)
            if self.on_error:
                self.on_error(f"google_tts_{snake(type(error).__name__)}")
            return {"last_spoken": text, "error": str(error)}

        if carry:
            frame = bytes(carry) + _MULAW_SILENCE * (FRAME_BYTES - len(carry))
            session.put_outbound_sync(frame)
            session.tap_agent(frame)
        logger.info(
            "Spoke (Google): chars=%d (%.2fs)", len(text), time.monotonic() - t0
        )

        try:
            session.send_mark_and_wait("tts_complete", timeout=30.0)
        except TimeoutError:
            logger.warning("Mark timeout — audio may have been cut off")

        if session.is_disconnected:
            return {"last_spoken": text, "call_disconnected": True}
        return {"last_spoken": text}
