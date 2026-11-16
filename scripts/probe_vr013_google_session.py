#!/usr/bin/env python3
"""VR-013 AC-12: live probe of an ordinary Google STT stream rotation.

Synthesizes ``"Kohta <word>."`` phrases with the real ``GoogleTTS`` (each
word distinct, no numbers), lays them out with 1.5 s of silence between
them over 300 s of 8 kHz mu-law audio, and feeds that audio at 20 ms per
frame through the real ``GooglePersistentStt``. The run passes when an
ordinary rotation (``forced=false``) is logged after 270 s and every phrase
word is committed exactly once, in order. Forced-rotation loss is not
measured live (VR-013 Q2, Amendment A-1).

Synthetic Finnish text only. Authentication is ADC; the project is also the
quota project.

Usage:
    python scripts/probe_vr013_google_session.py --project scp-tenant-csap-test \
        > tmp/vr013-live.log 2>&1
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata as metadata
import logging
import platform
import re
import statistics
import subprocess
import sys
import time
from collections import Counter

from voice_runtime.session import VoiceSession
from voice_runtime.stt import create_stt
from voice_runtime.tts import create_tts

FRAME = 160  # 20 ms of 8 kHz mu-law
FRAME_S = 0.02
SILENCE = b"\xff" * FRAME
GAP_FRAMES = 75  # 1.5 s of silence after each phrase
TOTAL_FRAMES = 15_000  # 300 s
RUN_TIMEOUT_S = 360.0
CLEANUP_TIMEOUT_S = 15.0
TAIL_WAIT_S = 6.0  # time for the last final after the input ends
ROTATE_AFTER_S = 270.0

WORDS = [
    "talo",
    "kissa",
    "koira",
    "metsä",
    "järvi",
    "kukka",
    "kirja",
    "lintu",
    "omena",
    "leipä",
    "maito",
    "auto",
    "vene",
    "juna",
    "silta",
    "kivi",
    "ranta",
    "meri",
    "saari",
    "vuori",
    "pilvi",
    "sade",
    "lumi",
    "tuuli",
    "aurinko",
    "tähti",
    "taivas",
    "kello",
    "tuoli",
    "pöytä",
    "ikkuna",
    "katto",
    "lattia",
    "lamppu",
    "sänky",
    "peili",
    "kuppi",
    "lusikka",
    "haarukka",
    "veitsi",
    "lautanen",
    "kattila",
    "uuni",
    "sauna",
    "koivu",
    "mänty",
    "vaahtera",
    "mustikka",
    "mansikka",
    "puolukka",
    "porkkana",
    "peruna",
    "sipuli",
    "tomaatti",
    "kurkku",
    "banaani",
    "appelsiini",
    "sitruuna",
    "kahvi",
    "mehu",
    "juusto",
    "kala",
    "lohi",
    "hauki",
    "ahven",
    "karhu",
    "susi",
    "kettu",
    "jänis",
    "hirvi",
    "orava",
    "siili",
    "pöllö",
    "varis",
    "joutsen",
    "sorsa",
    "lokki",
    "perhonen",
    "muurahainen",
    "mehiläinen",
    "hevonen",
    "lehmä",
    "lammas",
    "possu",
    "kana",
    "kukko",
    "tiikeri",
    "leijona",
    "norsu",
    "kirahvi",
    "apina",
    "delfiini",
    "valas",
    "kilpikonna",
    "sammakko",
    "käärme",
    "pallo",
    "nukke",
    "kitara",
    "rumpu",
    "piano",
    "viulu",
    "huilu",
    "laulu",
    "tarina",
    "runo",
    "kartta",
    "kompassi",
    "teltta",
    "reppu",
    "kenkä",
    "takki",
    "hattu",
    "huivi",
    "lapanen",
    "sukka",
    "paita",
    "hame",
    "vyö",
    "laukku",
    "sateenvarjo",
    "polkupyörä",
    "lentokone",
    "laiva",
    "kirkko",
    "koulu",
    "kirjasto",
    "tori",
    "kauppa",
    "puisto",
    "pelto",
    "niitty",
    "puro",
    "lähde",
]


class _CaptureSession(VoiceSession):
    """Collects TTS output once; no event loop, so marks are no-ops."""

    def __init__(self) -> None:
        super().__init__()
        self.audio = bytearray()

    def put_outbound_sync(self, data: bytes) -> None:
        self.audio.extend(data)


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.lines: list[tuple[float, int, str]] = []
        self.t0 = time.monotonic()

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(
            (time.monotonic() - self.t0, record.levelno, record.getMessage())
        )


def _instrument_tts_first_chunk(tts_module, marks: list[float]) -> None:
    """Record the time of the first non-empty SDK response of each call."""
    real_client_cls = tts_module.TextToSpeechClient

    class _Call:
        def __init__(self, call, started: float) -> None:
            self._call = call
            self._started = started
            self._seen = False

        def cancel(self):
            return self._call.cancel()

        def __iter__(self):
            for response in self._call:
                if not self._seen and response.audio_content:
                    self._seen = True
                    marks.append(time.monotonic() - self._started)
                yield response

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            self._client = real_client_cls(*args, **kwargs)
            self.transport = self._client.transport

        def streaming_synthesize(self, *args, **kwargs):
            started = time.monotonic()
            return _Call(self._client.streaming_synthesize(*args, **kwargs), started)

    tts_module.TextToSpeechClient = _Client


def synthesize_layout(project: str, ttfc: list[float]) -> tuple[bytes, list[str]]:
    tts = create_tts("google", project=project)
    from google.cloud import texttospeech

    _instrument_tts_first_chunk(texttospeech, ttfc)
    errors: list[str] = []
    tts.on_error = errors.append
    audio = bytearray()
    words: list[str] = []
    for word in WORDS:
        session = _CaptureSession()
        result = tts.speak(f"Kohta {word}.", session)
        if errors or set(result) != {"last_spoken"}:
            raise RuntimeError(f"TTS failed for {word!r}: {result} {errors}")
        clip = bytes(session.audio)
        if not clip or len(clip) % FRAME:
            raise RuntimeError(f"TTS capture for {word!r} has {len(clip)} bytes")
        needed = len(clip) // FRAME + GAP_FRAMES
        if len(audio) // FRAME + needed > TOTAL_FRAMES:
            break
        audio += clip + SILENCE * GAP_FRAMES
        words.append(word)
    else:
        raise RuntimeError("word list exhausted before 300 s were filled")
    audio += SILENCE * (TOTAL_FRAMES - len(audio) // FRAME)
    return bytes(audio), words


async def run_stt(
    project: str, audio: bytes, records: _Records
) -> tuple[list[str], list[str], float]:
    stt = create_stt("google", project=project)
    commits: list[str] = []
    errors: list[str] = []
    stt.on_committed = commits.append
    stt.on_error = errors.append
    inbound: asyncio.Queue[bytes | None] = asyncio.Queue()
    records.t0 = time.monotonic()
    await stt.start(inbound)
    feed_start = time.monotonic() - records.t0
    try:
        start = time.monotonic()
        for i in range(0, len(audio), FRAME):
            inbound.put_nowait(audio[i : i + FRAME])
            delay = start + (i // FRAME + 1) * FRAME_S - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            if errors:
                break
        inbound.put_nowait(None)
        await asyncio.sleep(TAIL_WAIT_S)
    finally:
        await asyncio.wait_for(stt.stop(), timeout=CLEANUP_TIMEOUT_S)
    return commits, errors, feed_start


def committed_words(commits: list[str]) -> list[str]:
    words: list[str] = []
    for text in commits:
        for part in re.findall(r"[^\W\d_]+", text.lower()):
            if part != "kohta":
                words.append(part)
    return words


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--project", required=True)
    args = parser.parse_args()

    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip()
    print(f"command: {' '.join([sys.executable, *sys.argv])}")
    print(f"git: {sha} python: {platform.python_version()}")
    for dist in (
        "voice-runtime",
        "google-cloud-speech",
        "google-cloud-texttospeech",
        "google-api-core",
    ):
        print(f"{dist} {metadata.version(dist)}")

    records = _Records()
    stt_logger = logging.getLogger("voice_runtime.providers.google_stt")
    stt_logger.addHandler(records)
    stt_logger.setLevel(logging.INFO)

    ttfc: list[float] = []
    t = time.monotonic()
    audio, expected = synthesize_layout(args.project, ttfc)
    print(
        f"tts: phrases={len(expected)} synth_s={time.monotonic() - t:.1f} "
        f"ttfc_ms first={ttfc[0] * 1000:.0f} median={statistics.median(ttfc) * 1000:.0f} "
        f"max={max(ttfc) * 1000:.0f}"
    )

    failures: list[str] = []
    try:
        commits, errors, feed_start = asyncio.run(
            asyncio.wait_for(
                run_stt(args.project, audio, records), timeout=RUN_TIMEOUT_S
            )
        )
    except TimeoutError:
        commits, errors, feed_start = [], [], 0.0
        failures.append(f"timeout after {RUN_TIMEOUT_S:.0f} s")

    opens = [ts for ts, _, msg in records.lines if msg == "Google STT stream open"]
    rotations = [(ts, msg) for ts, _, msg in records.lines if "stream rotate" in msg]
    print(f"feed_start_s={feed_start:.2f}")
    print("stream_open_s=" + ",".join(f"{ts:.2f}" for ts in opens))
    for ts, msg in rotations:
        age = ts - opens[0] if opens else float("nan")
        print(f"rotate at_s={ts:.2f} since_first_open_s={age:.2f}: {msg}")
    for ts, level, msg in records.lines:
        if level >= logging.WARNING:
            print(f"provider {logging.getLevelName(level)} at_s={ts:.2f}: {msg}")

    got = committed_words(commits)
    missing = sorted((Counter(expected) - Counter(got)).elements())
    extra = sorted((Counter(got) - Counter(expected)).elements())
    print(f"expected ({len(expected)}): {' '.join(expected)}")
    print(f"committed ({len(got)}): {' '.join(got)}")
    print(f"missing: {missing}")
    print(f"duplicated_or_unexpected: {extra}")
    print(f"errors: {errors}")

    if errors:
        failures.append(f"provider error {errors}")
    ordinary = [
        msg
        for ts, msg in rotations
        if "forced=false" in msg and opens and ts - opens[0] >= ROTATE_AFTER_S
    ]
    if not ordinary:
        failures.append("no ordinary rotation after 270 s")
    if any("forced=true" in msg for _, msg in rotations):
        failures.append("forced rotation occurred")
    if got != expected:
        failures.append("committed words differ from expected sequence")
    print("RESULT " + ("PASS" if not failures else "FAIL: " + "; ".join(failures)))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
