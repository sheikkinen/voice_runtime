"""Azure STT providers — persistent and per-turn modes.

NC-161: Azure Speech-to-Text using push stream with native MULAW 8kHz.

AzurePersistentStt: continuous recognition for full call duration.
  - No streaming duration limit (unlike ElevenLabs/GCP)
  - Push stream maps directly to inbound queue
  - Barge-in during TTS via partial transcripts
  - Echo discard window after TTS ends

AzurePerTurnStt: single-utterance recognition via recognize_once_async().
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
import time
from typing import Any

import azure.cognitiveservices.speech as speechsdk

logger = logging.getLogger(__name__)

AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY", "")
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "westeurope")
BARGE_IN_MIN_TEXT_LEN = 2
ECHO_DISCARD_WINDOW_S = 0.4


class AzurePersistentStt:
    """Azure Speech-to-Text provider for persistent mode.

    Uses continuous recognition with push stream for unlimited-duration
    streaming. Event-based partial/final transcripts map to barge-in
    and commit patterns.
    """

    def __init__(
        self,
        subscription_key: str | None = None,
        region: str | None = None,
        language_code: str = "fi-FI",
        silence_timeout_ms: int = 1500,
    ) -> None:
        self._subscription_key = subscription_key or os.getenv("AZURE_SPEECH_KEY", "")
        self._region = region or os.getenv("AZURE_SPEECH_REGION", "westeurope")
        self._language_code = language_code
        self._silence_timeout_ms = silence_timeout_ms
        self._loop: asyncio.AbstractEventLoop | None = None
        self._speaking = False
        self._direct_sent = False
        self.__listening = False
        self._barge_in_event: asyncio.Event | None = None
        self._transcript_queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._discard_until: float = 0.0
        self._push_stream: Any | None = None
        self._recognizer: Any | None = None
        self._feed_task: asyncio.Task[None] | None = None
        self._on_direct_dispatch: Any | None = None
        self._on_direct_transcribed: Any | None = None

    @property
    def _listening(self) -> bool:
        return self.__listening

    @_listening.setter
    def _listening(self, value: bool) -> None:
        if value:
            self._direct_sent = False
        self.__listening = value

    async def start(self, inbound_queue: asyncio.Queue[bytes | None]) -> None:
        """Open push stream, configure recognizer, begin feeding audio."""
        self._loop = asyncio.get_running_loop()

        # Audio format: MULAW 8kHz mono
        stream_format = speechsdk.audio.AudioStreamFormat(
            samples_per_second=8000,
            bits_per_sample=8,
            channels=1,
            wave_stream_format=speechsdk.AudioStreamWaveFormat.MULAW,
        )
        self._push_stream = speechsdk.audio.PushAudioInputStream(stream_format)
        audio_config = speechsdk.audio.AudioConfig(stream=self._push_stream)

        speech_config = speechsdk.SpeechConfig(
            subscription=self._subscription_key,
            region=self._region,
        )
        speech_config.speech_recognition_language = self._language_code
        speech_config.set_property(
            speechsdk.PropertyId.SpeechServiceConnection_EndSilenceTimeoutMs,
            str(self._silence_timeout_ms),
        )

        self._recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config,
            audio_config=audio_config,
        )
        self._recognizer.recognizing.connect(self._on_partial)
        self._recognizer.recognized.connect(self._on_committed)

        self._recognizer.start_continuous_recognition_async().get()

        self._feed_task = asyncio.create_task(
            self._feed_audio(inbound_queue), name="azure_stt_feed",
        )
        logger.info("AzurePersistentStt started (lang=%s)", self._language_code)

    async def stop(self) -> None:
        """Stop continuous recognition and close push stream."""
        if self._feed_task:
            self._feed_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._feed_task
        if self._recognizer:
            with contextlib.suppress(Exception):
                self._recognizer.stop_continuous_recognition_async().get()
        if self._push_stream:
            with contextlib.suppress(Exception):
                self._push_stream.close()
        logger.info("AzurePersistentStt stopped")

    def set_speaking(self, speaking: bool) -> None:
        """Toggle TTS speaking state for echo discard."""
        self._speaking = speaking
        if not speaking:
            self._barge_in_event = None
            self._discard_until = time.monotonic() + ECHO_DISCARD_WINDOW_S

    def arm_barge_in(self) -> asyncio.Event:
        """Return event that fires on partial transcript during TTS."""
        self._barge_in_event = asyncio.Event()
        return self._barge_in_event

    async def next_transcript(self, timeout: float = 30.0) -> str | None:
        """Await next committed transcript from recognized event."""
        self._listening = True
        try:
            return await asyncio.wait_for(self._transcript_queue.get(), timeout=timeout)
        except TimeoutError:
            return None
        finally:
            self._listening = False

    async def _feed_audio(self, inbound: asyncio.Queue[bytes | None]) -> None:
        """Feed inbound audio to Azure push stream."""
        frame_count = 0
        try:
            while True:
                frame = await inbound.get()
                if frame is None:
                    self._push_stream.close()
                    break
                frame_count += 1
                self._push_stream.write(frame)
        except asyncio.CancelledError:
            logger.info("_feed_audio: cancelled after %d frames", frame_count)

    def _on_partial(self, evt: Any) -> None:
        """Handle partial transcript — barge-in check."""
        text = evt.result.text
        if not self._speaking or len(text) <= BARGE_IN_MIN_TEXT_LEN:
            return
        if self._barge_in_event and not self._barge_in_event.is_set() and self._loop:
            self._loop.call_soon_threadsafe(self._barge_in_event.set)

    def _on_committed(self, evt: Any) -> None:
        """Handle committed transcript — dispatch or queue.

        NC-136: When not listening (between turns), direct-dispatch to FSM
        for mid-LLM user speech handling. Otherwise queue for next_transcript().
        """
        text = evt.result.text
        if self._speaking:
            return
        if time.monotonic() < self._discard_until:
            logger.info("discarding echo committed: %r", text)
            return
        cleaned = text.strip()
        if not cleaned:
            return

        if not self._listening and self._on_direct_dispatch and not self._direct_sent:
            try:
                self._on_direct_dispatch(cleaned)
                if self._on_direct_transcribed:
                    try:
                        self._on_direct_transcribed(cleaned)
                    except Exception as cb_exc:
                        logger.warning("direct transcribed callback failed: %s", cb_exc)
                self._direct_sent = True
                logger.info("direct transcribed send: %r", cleaned[:60])
                return
            except Exception as exc:
                logger.warning("direct transcribed send failed: %s", exc)

        self._transcript_queue.put_nowait(cleaned)


class AzurePerTurnStt:
    """Azure Speech-to-Text provider for per-turn mode.

    Uses recognize_once_async() for single-utterance recognition.
    Auto-stops on silence.
    """

    def __init__(
        self,
        subscription_key: str | None = None,
        region: str | None = None,
        language_code: str = "fi-FI",
    ) -> None:
        self._subscription_key = subscription_key or os.getenv("AZURE_SPEECH_KEY", "")
        self._region = region or os.getenv("AZURE_SPEECH_REGION", "westeurope")
        self._language_code = language_code

    def listen(self, session: Any, timeout: float = 30.0) -> str:
        """Feed audio to push stream, return recognized text."""
        loop = session.loop
        if loop is None:
            return ""

        stream_format = speechsdk.audio.AudioStreamFormat(
            samples_per_second=8000,
            bits_per_sample=8,
            channels=1,
            wave_stream_format=speechsdk.AudioStreamWaveFormat.MULAW,
        )
        push_stream = speechsdk.audio.PushAudioInputStream(stream_format)
        audio_config = speechsdk.audio.AudioConfig(stream=push_stream)

        speech_config = speechsdk.SpeechConfig(
            subscription=self._subscription_key,
            region=self._region,
        )
        speech_config.speech_recognition_language = self._language_code

        recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config,
            audio_config=audio_config,
        )

        # Feed audio in a background thread
        stop = threading.Event()

        def feed_audio() -> None:
            while not stop.is_set():
                if session.is_disconnected:
                    push_stream.close()
                    return
                try:
                    frame = session.inbound.get_nowait()
                except Exception:
                    stop.wait(0.05)
                    continue
                if frame is None:
                    push_stream.close()
                    return
                push_stream.write(frame)
            push_stream.close()

        feed_thread = threading.Thread(target=feed_audio, daemon=True)
        feed_thread.start()

        try:
            result = recognizer.recognize_once_async().get()
            if result.reason == speechsdk.ResultReason.RecognizedSpeech:
                return result.text
            return ""
        except Exception as e:
            logger.error("AzurePerTurnStt error: %s", e)
            return ""
        finally:
            stop.set()
            feed_thread.join(timeout=2.0)
