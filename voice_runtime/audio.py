"""G.711 μ-law codec and real-time audio mixer.

NC-152: Extracted from outcaller/nodes/audio_mixer.py (228 lines, zero
project imports). Provides mulaw encode/decode, frame mixing, and the
AudioMixer class for local audio monitoring.

NC-235: Optional WAV file recording (record_path parameter).
Shared by outcaller and ninchat_voice (vendored). Additive change —
record_path=None default preserves existing behavior. Cross-project
impact disappears post NC-230 extraction.

Architecture:
  tap_caller(chunk) → caller deque ─┐
                                     ├─ mix thread (20ms tick) → ffplay stdin
  tap_agent(chunk)  → agent deque  ─┘                          → WAV file (opt)
"""

from __future__ import annotations

import collections
import contextlib
import logging
import struct
import subprocess
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

FRAME_BYTES: int = 160          # 20ms @ 8kHz, 1 byte/sample (mulaw)
FRAME_INTERVAL: float = 0.020   # 20ms
MAX_FRAMES: int = 400           # 8 seconds at 20ms/frame
SILENCE_FRAME: bytes = b"\xff" * FRAME_BYTES  # mulaw silence (zero amplitude)
CATCHUP_LIMIT: float = 5 * FRAME_INTERVAL     # 100ms — reset threshold


# ---------------------------------------------------------------------------
# G.711 μ-law codec (ITU-T, public domain tables)
# Inline implementation — avoids audioop dependency removed in Python 3.13.
# ---------------------------------------------------------------------------

_ULAW_TO_LINEAR: list[int] = []


def _build_ulaw_table() -> None:
    """Populate _ULAW_TO_LINEAR (256 entries) from the G.711 formula."""
    _exp_lut = [0, 132, 396, 924, 1980, 4092, 8316, 16764]
    for i in range(256):
        val = ~i
        sign = val & 0x80
        exponent = (val >> 4) & 0x07
        mantissa = val & 0x0F
        sample = _exp_lut[exponent] + (mantissa << (exponent + 3))
        if sign != 0:
            sample = -sample
        _ULAW_TO_LINEAR.append(sample)


_build_ulaw_table()


def _linear_to_ulaw(sample: int) -> int:
    """Encode a single 16-bit signed sample to μ-law byte."""
    BIAS = 0x84
    CLIP = 32635
    sign = 0
    if sample < 0:
        sign = 0x80
        sample = -sample
    if sample > CLIP:
        sample = CLIP
    sample += BIAS
    exponent = 7
    exp_mask = 0x4000
    for _ in range(8):
        if sample & exp_mask:
            break
        exponent -= 1
        exp_mask >>= 1
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


def mix_frames(caller: bytes, agent: bytes) -> bytes:
    """Mix two mulaw frames by decoding, adding (clamped), re-encoding.

    Args:
        caller: FRAME_BYTES of mulaw caller audio
        agent: FRAME_BYTES of mulaw agent audio

    Returns:
        FRAME_BYTES of mixed mulaw audio
    """
    out = bytearray(FRAME_BYTES)
    for i in range(FRAME_BYTES):
        c = _ULAW_TO_LINEAR[caller[i]]
        a = _ULAW_TO_LINEAR[agent[i]]
        mixed = c + a
        if mixed > 32767:
            mixed = 32767
        elif mixed < -32768:
            mixed = -32768
        out[i] = _linear_to_ulaw(mixed)
    return bytes(out)


def _write_wav_header(f, data_size: int) -> None:
    """Write a WAV header for 8kHz mono mulaw audio.

    WAV mulaw: codec ID 7, 8000 Hz, 1 channel, 8 bits/sample.
    fmt chunk is 18 bytes (includes cbSize=0 for non-PCM).
    """
    fmt_size = 18
    header_size = 4 + (8 + fmt_size) + (8 + data_size)  # WAVE + fmt chunk + data chunk
    f.seek(0)
    # RIFF header
    f.write(struct.pack("<4sI4s", b"RIFF", header_size, b"WAVE"))
    # fmt chunk: codec=7(mulaw), channels=1, rate=8000, byterate=8000, blockalign=1, bits=8, cbSize=0
    f.write(struct.pack("<4sIHHIIHHH", b"fmt ", fmt_size, 7, 1, 8000, 8000, 1, 8, 0))
    # data chunk header
    f.write(struct.pack("<4sI", b"data", data_size))


class AudioMixer:
    """Real-time mulaw mixer: two input channels → one mixed output.

    Uses bounded deques (not ring buffers) — correct for bursty agent audio.
    Each direction has an independent FIFO. The mix thread pops one frame from
    each deque every 20ms. Empty deque → silence. Overfull deque (agent burst)
    → frames accumulate and drain at 1×; maxlen drops oldest on overflow.

    NC-235: When record_path is set, mixed audio is also written to a WAV file.
    """

    def __init__(self, record_path: Path | None = None) -> None:
        self._caller: collections.deque[bytes] = collections.deque(maxlen=MAX_FRAMES)
        self._agent: collections.deque[bytes] = collections.deque(maxlen=MAX_FRAMES)
        self._proc: subprocess.Popen[bytes] | None = None
        self._thread: threading.Thread | None = None
        self._running: bool = False
        # NC-235: WAV recording
        self._record_path = record_path
        self._record_file = None
        self._record_bytes: int = 0

    def start(self) -> None:
        """Start ffplay and the mix drain thread."""
        import shutil
        if not shutil.which("ffplay"):
            raise RuntimeError(
                "ffplay not found — install ffmpeg to use AudioMixer: "
                "https://ffmpeg.org/download.html"
            )
        cmd = [
            "ffplay", "-nodisp",
            "-probesize", "32",
            "-fflags", "nobuffer",
            "-f", "mulaw", "-ar", "8000", "-",
        ]
        self._proc = subprocess.Popen(  # noqa: S603
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        # NC-235: Open recording file
        if self._record_path:
            self._record_path.parent.mkdir(parents=True, exist_ok=True)
            self._record_file = open(self._record_path, "wb")  # noqa: SIM115
            _write_wav_header(self._record_file, 0)  # placeholder header
            logger.info("AudioMixer recording to %s", self._record_path)

        self._running = True
        self._thread = threading.Thread(target=self._mix_loop, daemon=True)
        self._thread.start()
        logger.info("AudioMixer started (pid=%d)", self._proc.pid)

    def write_caller(self, chunk: bytes) -> None:
        """Enqueue caller mulaw frames (160B each) into the caller deque."""
        for i in range(0, len(chunk), FRAME_BYTES):
            frame = chunk[i : i + FRAME_BYTES]
            if len(frame) < FRAME_BYTES:
                frame = frame + b"\xff" * (FRAME_BYTES - len(frame))
            self._caller.append(frame)

    def write_agent(self, chunk: bytes) -> None:
        """Enqueue agent mulaw frames (variable size) into the agent deque."""
        for i in range(0, len(chunk), FRAME_BYTES):
            frame = chunk[i : i + FRAME_BYTES]
            if len(frame) < FRAME_BYTES:
                frame = frame + b"\xff" * (FRAME_BYTES - len(frame))
            self._agent.append(frame)

    def _mix_loop(self) -> None:
        """Every 20ms: pop one frame from each deque, mix, write to ffplay."""
        next_tick = time.monotonic()

        while self._running and self._proc and self._proc.stdin:
            now = time.monotonic()

            if now > next_tick + CATCHUP_LIMIT:
                next_tick = now
            elif now < next_tick:
                time.sleep(next_tick - now)

            next_tick += FRAME_INTERVAL

            try:
                caller_frame = self._caller.popleft()
            except IndexError:
                caller_frame = SILENCE_FRAME

            try:
                agent_frame = self._agent.popleft()
            except IndexError:
                agent_frame = SILENCE_FRAME

            if caller_frame is SILENCE_FRAME and agent_frame is SILENCE_FRAME:
                mixed = SILENCE_FRAME
            elif agent_frame is SILENCE_FRAME:
                mixed = caller_frame
            elif caller_frame is SILENCE_FRAME:
                mixed = agent_frame
            else:
                mixed = mix_frames(caller_frame, agent_frame)

            try:
                self._proc.stdin.write(mixed)
            except (BrokenPipeError, OSError):
                logger.warning("AudioMixer: ffplay pipe broken")
                self._running = False
                break

            # NC-235: Write to recording file
            if self._record_file:
                try:
                    self._record_file.write(mixed)
                    self._record_bytes += len(mixed)
                except OSError:
                    logger.warning("AudioMixer: recording write failed")
                    self._record_file = None

    def shutdown(self) -> None:
        """Stop mix thread, finalize recording, and stop ffplay process."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)

        # NC-235: Finalize WAV header with actual data size
        if self._record_file:
            try:
                _write_wav_header(self._record_file, self._record_bytes)
                self._record_file.close()
                duration_s = self._record_bytes / 8000
                logger.info(
                    "AudioMixer recording saved: %s (%.1fs, %d bytes)",
                    self._record_path, duration_s, self._record_bytes,
                )
            except OSError:
                logger.warning("AudioMixer: failed to finalize recording")
            self._record_file = None
        if self._proc:
            pid = self._proc.pid
            with contextlib.suppress(OSError):
                if self._proc.stdin:
                    self._proc.stdin.close()
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                # NC-170 Fix 5: SIGKILL after terminate timeout
                logger.warning("AudioMixer: ffplay pid=%d did not terminate, sending SIGKILL", pid)
                self._proc.kill()
                with contextlib.suppress(Exception):
                    self._proc.wait(timeout=1.0)
            logger.info("AudioMixer shutdown (pid=%d)", pid)
