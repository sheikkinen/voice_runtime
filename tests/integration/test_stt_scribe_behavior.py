"""Integration test: PersistentSttSession with real ElevenLabs Scribe API.

Feeds pre-baked mulaw audio (speech + silence patterns) to observe actual
committed/partial event data dicts from the API. No mocking — this tests
the real pipeline.

Pattern — 180s simulated call with speech checkpoints across three phases:

  Phase 1 (0–60s):   greeting → symptom → medication → preference
  Phase 2 (60–120s): personal details → date confirm → elaboration
  Phase 3 (120–180s): wrap-up → goodbye → late additions

Long pauses (10–20s) simulate agent TTS responses between caller turns.
Fed at ~3x speed → ~65s wall-clock time.

All timestamps are relative to test start (T+0.0s).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

FIXTURES = Path(__file__).parent.parent / "fixtures"
MULAW_SILENCE = b"\xff"  # u-law digital zero
SAMPLE_RATE = 8000  # 8 kHz

# Test start time — set in the test, used by _ts()
_t0: float = 0.0


def _ts() -> str:
    """Return elapsed timestamp string like 'T+12.3s'."""
    return f"T+{time.monotonic() - _t0:.1f}s"


def _load_speech(name: str) -> bytes:
    path = FIXTURES / f"{name}.ulaw"
    if not path.exists():
        pytest.skip(f"Fixture {path} not found — run gen_speech.py first")
    return path.read_bytes()


def _silence(seconds: float) -> bytes:
    return MULAW_SILENCE * int(SAMPLE_RATE * seconds)


@pytest.mark.skipif(
    not os.getenv("ELEVENLABS_API_KEY"),
    reason="ELEVENLABS_API_KEY required",
)
@pytest.mark.asyncio
async def test_scribe_committed_events_over_long_session():
    """Feed 180s simulated call and check for quality degradation.

    Hypothesis under test: STT quality degrades after ~60s.
    Speech checkpoints at ~10s, ~30s, ~55s, ~80s, ~105s, ~130s, ~155s, ~175s
    allow comparing early vs late transcription quality.
    """
    global _t0

    from voice_runtime.providers.elevenlabs_stt import (
        PersistentSttSession,
    )

    # Build the audio pattern — 180s call with realistic TTS pauses
    s0 = _load_speech("speech_0")  # "Hei, olen Sami." (1.3s)
    s1 = _load_speech("speech_1")  # "Tarvitsisin apua hammaslääkäriajan..." (2.8s)
    s2 = _load_speech("speech_2")  # "Ensi viikon tiistai sopisi hyvin." (2.0s)
    s3 = _load_speech("speech_3")  # "Minulla on ollut kovaa päänsärkyä..." (5.3s)
    s4 = _load_speech("speech_4")  # "Kyllä, olen kokeillut ibuprofeenia..." (3.8s)
    s5 = _load_speech("speech_5")  # "Maanantai aamupäivä olisi paras..." (3.6s)
    s6 = _load_speech("speech_6")  # "Nimeni on Matti Virtanen..." (6.9s)
    s7 = _load_speech("speech_7")  # "Kiitos paljon avusta..." (2.8s)

    # --- Phase 1: 0–60s (early) ---
    segments = [
        # ~0s: greeting
        ("PHASE_1_START", s0),  # 1.3s  → ~1s
        ("agent_responds_15s", _silence(15)),  #        → ~16s
        # ~16s: symptom description
        ("symptom_description", s3),  # 5.3s  → ~22s
        ("agent_responds_12s", _silence(12)),  #        → ~34s
        # ~34s: medication answer
        ("medication_answer", s4),  # 3.8s  → ~37s
        ("agent_responds_15s", _silence(15)),  #        → ~52s
        # ~52s: time preference
        ("time_preference", s5),  # 3.6s  → ~56s
        ("agent_responds_4s", _silence(4)),  #        → ~60s
        # --- Phase 2: 60–120s (mid-session) ---
        # ~60s: personal details (long utterance)
        ("PHASE_2_START", s6),  # 6.9s  → ~67s
        ("agent_responds_15s", _silence(15)),  #        → ~82s
        # ~82s: confirm date
        ("confirm_date", s2),  # 2.0s  → ~84s
        ("agent_responds_12s", _silence(12)),  #        → ~96s
        # ~96s: elaborate on request
        ("elaborate_request", s1),  # 2.8s  → ~99s
        ("agent_responds_15s", _silence(15)),  #        → ~114s
        # --- Phase 3: 120–180s (late session — degradation window) ---
        # ~114s: follow-up
        ("PHASE_3_START", s3),  # 5.3s  → ~119s
        ("agent_responds_20s", _silence(20)),  #        → ~139s
        # ~139s: goodbye
        ("goodbye", s7),  # 2.8s  → ~142s
        ("long_agent_farewell_15s", _silence(15)),  #        → ~157s
        # ~157s: late repeat — same utterance as opening
        ("late_repeat_greeting", s0),  # 1.3s  → ~158s
        ("agent_responds_12s", _silence(12)),  #        → ~170s
        # ~170s: final utterance — complex, name+date
        ("final_utterance", s6),  # 6.9s  → ~177s
        ("trailing_3s", _silence(3)),  #        → ~180s
    ]

    total_bytes = sum(len(d) for _, d in segments)
    total_secs = total_bytes / SAMPLE_RATE
    logger.info("[%s] Total audio: %.1fs (%d bytes)", _ts(), total_secs, total_bytes)

    # Collect ALL events with full data dicts
    events: list[dict] = []

    def _record(event_type: str, data: dict) -> None:
        entry = {
            "t": time.monotonic(),
            "type": event_type,
            "data": dict(data),  # copy
        }
        events.append(entry)
        text = data.get("text", "")
        logger.info(
            "[%s] EVENT %s: text=%r",
            _ts(),
            event_type,
            text[:80] if text else "",
        )

    # Create STT session and monkey-patch callbacks to record events
    stt = PersistentSttSession()
    original_on_partial = stt._on_partial
    original_on_committed = stt._on_committed

    def patched_partial(data: dict) -> None:
        _record("partial_transcript", data)
        original_on_partial(data)

    def patched_committed(data: dict) -> None:
        _record("committed_transcript", data)
        original_on_committed(data)

    stt._on_partial = patched_partial
    stt._on_committed = patched_committed

    # Feed audio via async queue
    inbound: asyncio.Queue[bytes | None] = asyncio.Queue()

    _t0 = time.monotonic()
    await stt.start(inbound)

    # Feed segments in ~160-byte chunks (20ms at 8kHz)
    CHUNK_SIZE = 160

    for seg_name, seg_data in segments:
        seg_dur = len(seg_data) / SAMPLE_RATE
        logger.info("[%s] >>> Feeding: %s (%.1fs)", _ts(), seg_name, seg_dur)
        for i in range(0, len(seg_data), CHUNK_SIZE):
            chunk = seg_data[i : i + CHUNK_SIZE]
            await inbound.put(chunk)
            # Pace at ~real-time (20ms per chunk) at 1.5x speed
            # Slower rate avoids queue_overflow on long sessions
            await asyncio.sleep(0.013)

    # Send sentinel
    await inbound.put(None)
    logger.info("[%s] Sentinel sent, waiting for final events...", _ts())

    # Wait for remaining events
    await asyncio.sleep(3.0)

    # Stop STT
    await stt.stop()

    elapsed = time.monotonic() - _t0
    logger.info("[%s] === Test completed in %.1fs ===", _ts(), elapsed)

    # Analyze results
    committed_events = [e for e in events if e["type"] == "committed_transcript"]
    partial_events = [e for e in events if e["type"] == "partial_transcript"]

    logger.info("")
    logger.info("=== COMMITTED EVENTS (%d) ===", len(committed_events))
    for i, ev in enumerate(committed_events):
        text = ev["data"].get("text", "")
        ts = f"T+{ev['t'] - _t0:.1f}s"
        logger.info("  [%d] %s  %s", i, ts, text if text else "(empty)")

    logger.info("")
    logger.info("=== PARTIAL EVENTS (%d) ===", len(partial_events))
    for i, ev in enumerate(partial_events):
        text = ev["data"].get("text", "")
        ts = f"T+{ev['t'] - _t0:.1f}s"
        logger.info("  [%d] %s  %s", i, ts, text[:60] if text else "(empty)")

    # Summary
    non_empty = [e for e in committed_events if e["data"].get("text", "").strip()]
    logger.info("")
    logger.info("=== SUMMARY ===")
    logger.info("Non-empty committed: %d / %d", len(non_empty), len(committed_events))
    logger.info("Partial events: %d", len(partial_events))

    # Phase analysis — check quality at different time offsets
    # Audio time = event timestamp × feed_speed_multiplier (~3x)
    # But we compare within real time since that's what matters
    phase_1 = [e for e in non_empty if e["t"] - _t0 < 25]  # first ~60s audio
    phase_2 = [e for e in non_empty if 25 <= e["t"] - _t0 < 45]  # ~60-120s audio
    phase_3 = [e for e in non_empty if e["t"] - _t0 >= 45]  # ~120-180s audio

    logger.info("")
    logger.info("=== PHASE ANALYSIS (degradation check) ===")
    logger.info("Phase 1 (0-60s audio):   %d non-empty commits", len(phase_1))
    for e in phase_1:
        logger.info("  T+%.1fs  %s", e["t"] - _t0, e["data"].get("text", "")[:70])
    logger.info("Phase 2 (60-120s audio): %d non-empty commits", len(phase_2))
    for e in phase_2:
        logger.info("  T+%.1fs  %s", e["t"] - _t0, e["data"].get("text", "")[:70])
    logger.info("Phase 3 (120-180s audio): %d non-empty commits", len(phase_3))
    for e in phase_3:
        logger.info("  T+%.1fs  %s", e["t"] - _t0, e["data"].get("text", "")[:70])

    # We have 11 speech segments total — expect at least 6 non-empty
    assert len(non_empty) >= 6, (
        f"Expected at least 6 non-empty committed transcripts, got {len(non_empty)}. "
        f"All committed: {[e['data'].get('text', '') for e in committed_events]}"
    )

    # Degradation check: phase 3 should have at least 1 non-empty
    if len(phase_3) < 2:
        logger.warning(
            "⚠ POSSIBLE DEGRADATION: Phase 3 (120-180s) had only %d non-empty "
            "commits vs Phase 1's %d. Investigate if queue_overflow occurred.",
            len(phase_3),
            len(phase_1),
        )
    assert len(phase_3) >= 1, (
        f"DEGRADATION DETECTED: Phase 3 (120-180s) had {len(phase_3)} non-empty commits. "
        f"Phase 1 had {len(phase_1)}, Phase 2 had {len(phase_2)}."
    )
