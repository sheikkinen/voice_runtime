"""RED phase tests for voice_runtime.audio — G.711 μ-law codec + AudioMixer.

Extracted from outcaller/tests/unit/test_audio_mixer.py.
Tests adapted to import from voice_runtime.audio instead of outcaller.

NC-152 Phase 2, Step 1.
"""

from __future__ import annotations

import subprocess
import time
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# G.711 μ-law codec
# ---------------------------------------------------------------------------


class TestG711Codec:
    def test_silence_byte_decodes_to_zero(self):
        from projects.voice_runtime.audio import _ULAW_TO_LINEAR

        assert _ULAW_TO_LINEAR[0xFF] == 0

    def test_encode_zero_returns_silence(self):
        from projects.voice_runtime.audio import _linear_to_ulaw

        assert _linear_to_ulaw(0) == 0xFF

    def test_roundtrip_preserves_sign(self):
        from projects.voice_runtime.audio import _ULAW_TO_LINEAR, _linear_to_ulaw

        for sample in [100, 1000, 10000, 30000, -100, -1000, -10000, -30000]:
            encoded = _linear_to_ulaw(sample)
            decoded = _ULAW_TO_LINEAR[encoded]
            if sample > 0:
                assert decoded > 0, f"sample={sample} → {encoded} → {decoded}"
            elif sample < 0:
                assert decoded < 0, f"sample={sample} → {encoded} → {decoded}"

    def test_decode_table_has_256_entries(self):
        from projects.voice_runtime.audio import _ULAW_TO_LINEAR

        assert len(_ULAW_TO_LINEAR) == 256

    def test_encoder_clips_large_values(self):
        from projects.voice_runtime.audio import _linear_to_ulaw

        a = _linear_to_ulaw(32635)
        b = _linear_to_ulaw(40000)
        assert a == b


# ---------------------------------------------------------------------------
# mix_frames
# ---------------------------------------------------------------------------


class TestMixFrames:
    def test_silence_plus_silence_is_silence(self):
        from projects.voice_runtime.audio import SILENCE_FRAME, mix_frames

        result = mix_frames(SILENCE_FRAME, SILENCE_FRAME)
        assert result == SILENCE_FRAME

    def test_silence_plus_audio_is_not_silence(self):
        from projects.voice_runtime.audio import SILENCE_FRAME, mix_frames

        agent = bytes(range(160))
        result = mix_frames(SILENCE_FRAME, agent)
        assert len(result) == 160
        assert result != SILENCE_FRAME

    def test_audio_plus_silence_is_not_silence(self):
        from projects.voice_runtime.audio import SILENCE_FRAME, mix_frames

        caller = bytes(range(160))
        result = mix_frames(caller, SILENCE_FRAME)
        assert len(result) == 160
        assert result != SILENCE_FRAME

    def test_mix_frames_clamps_overflow(self):
        from projects.voice_runtime.audio import _ULAW_TO_LINEAR, mix_frames

        max_byte = max(range(256), key=lambda b: _ULAW_TO_LINEAR[b])
        loud = bytes([max_byte] * 160)
        result = mix_frames(loud, loud)
        for b in result:
            assert _ULAW_TO_LINEAR[b] <= 32767

    def test_mix_frames_clamps_underflow(self):
        from projects.voice_runtime.audio import _ULAW_TO_LINEAR, mix_frames

        min_byte = min(range(256), key=lambda b: _ULAW_TO_LINEAR[b])
        loud_neg = bytes([min_byte] * 160)
        result = mix_frames(loud_neg, loud_neg)
        for b in result:
            assert _ULAW_TO_LINEAR[b] >= -32768

    def test_output_length(self):
        from projects.voice_runtime.audio import FRAME_BYTES, mix_frames

        result = mix_frames(bytes([0x80] * FRAME_BYTES), bytes([0x40] * FRAME_BYTES))
        assert len(result) == FRAME_BYTES


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_silence_frame_constant(self):
        from projects.voice_runtime.audio import FRAME_BYTES, SILENCE_FRAME

        assert SILENCE_FRAME == b"\xff" * FRAME_BYTES
        assert len(SILENCE_FRAME) == 160

    def test_frame_bytes(self):
        from projects.voice_runtime.audio import FRAME_BYTES

        assert FRAME_BYTES == 160

    def test_frame_interval(self):
        from projects.voice_runtime.audio import FRAME_INTERVAL

        assert FRAME_INTERVAL == 0.020


# ---------------------------------------------------------------------------
# AudioMixer write methods
# ---------------------------------------------------------------------------


class TestWriteMethods:
    def test_write_caller_enqueues_single_frame(self):
        from projects.voice_runtime.audio import AudioMixer, FRAME_BYTES

        m = AudioMixer()
        m.write_caller(b"\x01" * FRAME_BYTES)
        assert len(m._caller) == 1

    def test_write_agent_enqueues_single_frame(self):
        from projects.voice_runtime.audio import AudioMixer, FRAME_BYTES

        m = AudioMixer()
        m.write_agent(b"\x02" * FRAME_BYTES)
        assert len(m._agent) == 1

    def test_write_caller_splits_large_chunk(self):
        from projects.voice_runtime.audio import AudioMixer, FRAME_BYTES

        m = AudioMixer()
        m.write_caller(b"\x03" * (FRAME_BYTES * 4))
        assert len(m._caller) == 4

    def test_write_caller_pads_partial_frame(self):
        from projects.voice_runtime.audio import AudioMixer, FRAME_BYTES

        m = AudioMixer()
        m.write_caller(b"\x05" * 100)
        assert len(m._caller) == 1
        frame = m._caller[0]
        assert len(frame) == FRAME_BYTES
        assert frame[:100] == b"\x05" * 100
        assert frame[100:] == b"\xff" * 60

    def test_deque_maxlen_drops_oldest(self):
        from projects.voice_runtime.audio import AudioMixer, FRAME_BYTES, MAX_FRAMES

        m = AudioMixer()
        for i in range(MAX_FRAMES + 1):
            m.write_agent(bytes([i % 256]) * FRAME_BYTES)
        assert len(m._agent) == MAX_FRAMES
        assert m._agent[0] == bytes([1]) * FRAME_BYTES


# ---------------------------------------------------------------------------
# AudioMixer lifecycle
# ---------------------------------------------------------------------------


class TestMixerLifecycle:
    def test_start_launches_ffplay(self):
        from projects.voice_runtime.audio import AudioMixer

        m = AudioMixer()
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.stdin = MagicMock()
        mock_proc.pid = 99999
        with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
            m.start()
            assert mock_popen.call_count == 1
            cmd = mock_popen.call_args[0][0]
            assert cmd[0] == "ffplay"
            m._running = False
            if m._thread:
                m._thread.join(timeout=1.0)

    def test_shutdown_stops_thread_and_process(self):
        from projects.voice_runtime.audio import AudioMixer

        m = AudioMixer()
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.stdin = MagicMock()
        mock_proc.pid = 99999
        with patch("subprocess.Popen", return_value=mock_proc):
            m.start()
            time.sleep(0.05)
            m.shutdown()
        assert m._running is False
        mock_proc.terminate.assert_called_once()


# ---------------------------------------------------------------------------
# Mix loop
# ---------------------------------------------------------------------------


class TestMixLoop:
    def test_empty_deques_produce_silence(self):
        from projects.voice_runtime.audio import AudioMixer, SILENCE_FRAME

        m = AudioMixer()
        written: list[bytes] = []
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock(side_effect=lambda d: written.append(d))
        mock_proc.pid = 99999

        with patch("subprocess.Popen", return_value=mock_proc):
            m.start()
            time.sleep(0.08)
            m.shutdown()

        assert len(written) >= 2
        for frame in written:
            assert frame == SILENCE_FRAME

    def test_broken_pipe_stops_loop(self):
        from projects.voice_runtime.audio import AudioMixer

        m = AudioMixer()
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock(side_effect=BrokenPipeError)
        mock_proc.pid = 99999

        with patch("subprocess.Popen", return_value=mock_proc):
            m.start()
            time.sleep(0.05)

        assert m._running is False
