#!/usr/bin/env python3
"""VR-013 AC-12: live probe of an ordinary Google STT stream rotation.

Synthesizes ``"Kohta <word>."`` phrases with the real ``GoogleTTS`` (each
word distinct, no numbers), lays them out with 1.5 s of silence between
them over 300 s of 8 kHz mu-law audio, and feeds that audio at 20 ms per
frame through the real ``GooglePersistentStt``. The run passes when there is
no provider error or timeout, one ordinary rotation (``forced=false``) at a
stream age of at least 270 s, no forced rotation, and no run of 3 or more
consecutive unmatched target words under ``score()`` (VR-013 §4, Amendment
A-2 as folded in judgement round 3). Forced-rotation loss is not measured
live (VR-013 Q2, Amendment A-1).

``--rescore LOG`` re-scores the expected and committed lines of an earlier
run's log offline, with no network access.

Synthetic Finnish text only. Authentication is ADC; the project is also the
quota project.

Usage:
    python scripts/probe_vr013_google_session.py --project scp-tenant-csap-test \
        > tmp/vr013-live.log 2>&1
    python scripts/probe_vr013_google_session.py --rescore tmp/vr013-live.log
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
from array import array
from dataclasses import dataclass
from pathlib import Path

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
CARRIER = "Kohta"
LOSS_RUN = 3  # consecutive unmatched targets that count as a loss signal
SCORER = (
    "align(): max-weight char alignment on casefolded isalnum chars "
    "(target 2, carrier 1, +1 per continued run; deterministic trace-back)"
)

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


def normalise(text: str) -> str:
    """Casefold and keep only alphanumerics (Finnish letters stay)."""
    return "".join(ch for ch in text.casefold() if ch.isalnum())


@dataclass(frozen=True)
class Score:
    targets: list[str]
    matched: list[bool]
    inserted: list[str]
    duplicates: list[str]

    @property
    def matched_count(self) -> int:
        return sum(self.matched)

    @property
    def longest_unmatched_run(self) -> int:
        longest = run = 0
        for ok in self.matched:
            run = 0 if ok else run + 1
            longest = max(longest, run)
        return longest


def align(ref: str, hyp: str, weights: list[int]) -> tuple[list[bool], list[bool]]:
    """Maximum-weight character alignment of ``ref`` and ``hyp``.

    A matched character scores its weight (target 2, carrier 1) plus a bonus
    of 1 when it continues a run of matches, so whole words win over scattered
    characters and a recognised target is never given up for a carrier. Two
    states per cell: ending in a match (M) or not (N). The trace-back from the
    end prefers M, then a continued run, then skipping a reference character,
    then skipping a hypothesis character, so the result is deterministic.
    """
    n, m = len(ref), len(hyp)
    neg = -(10**9)
    big_m = [array("l", [neg] * (m + 1))]
    big_n = [array("l", [0] * (m + 1))]
    for i in range(1, n + 1):
        mp, np_, ch, w = big_m[-1], big_n[-1], ref[i - 1], weights[i - 1]
        mc, nc = array("l", [neg] * (m + 1)), array("l", [0] * (m + 1))
        for j in range(1, m + 1):
            nc[j] = max(mp[j], np_[j], mc[j - 1], nc[j - 1])
            if hyp[j - 1] == ch:
                mc[j] = max(mp[j - 1] + w + 1, np_[j - 1] + w)
        big_m.append(mc)
        big_n.append(nc)
    ref_hit, hyp_hit = [False] * n, [False] * m
    i, j = n, m
    in_match = big_m[n][m] >= big_n[n][m]
    while i and j:
        if in_match:
            ref_hit[i - 1] = hyp_hit[j - 1] = True
            in_match = big_m[i - 1][j - 1] + weights[i - 1] + 1 == big_m[i][j]
            i, j = i - 1, j - 1
            continue
        value = big_n[i][j]
        if big_m[i - 1][j] == value:
            i, in_match = i - 1, True
        elif big_n[i - 1][j] == value:
            i -= 1
        elif big_m[i][j - 1] == value:
            j, in_match = j - 1, True
        else:
            j -= 1
    return ref_hit, hyp_hit


def score(targets: list[str], committed: list[str], carrier: str = CARRIER) -> Score:
    """Whitespace-insensitive scoring of committed texts against the phrases.

    The reference is ``carrier + target`` per phrase, normalised; only the
    targets are scored. A target matches when every one of its characters is
    aligned by ``align()``. Recognised text is used as committed: no prefix is
    stripped.
    """
    ref_chars: list[str] = []
    owner: list[int] = []
    for index, word in enumerate(targets):
        for ch in normalise(carrier):
            ref_chars.append(ch)
            owner.append(-1)
        for ch in normalise(word):
            ref_chars.append(ch)
            owner.append(index)
    ref = "".join(ref_chars)
    hyp = normalise(" ".join(committed))
    weights = [1 if index < 0 else 2 for index in owner]
    ref_hit, hyp_hit = align(ref, hyp, weights)
    matched = [True] * len(targets)
    for pos, index in enumerate(owner):
        if index >= 0 and not ref_hit[pos]:
            matched[index] = False
    inserted: list[str] = []
    span = ""
    for ch, hit in zip(hyp, hyp_hit, strict=True):
        if hit and span:
            inserted.append(span)
            span = ""
        elif not hit:
            span += ch
    if span:
        inserted.append(span)
    duplicates = [w for w in targets if hyp.count(normalise(w)) > 1]
    return Score(targets, matched, inserted, duplicates)


def print_score(result: Score) -> None:
    total = len(result.targets)
    share = result.matched_count / total if total else 0.0
    print(f"scorer: {SCORER}; carrier={CARRIER!r}; loss_run={LOSS_RUN}")
    for word, ok in zip(result.targets, result.matched, strict=True):
        print(f"  {'ok  ' if ok else 'MISS'} {word}")
    unmatched = [
        w for w, ok in zip(result.targets, result.matched, strict=True) if not ok
    ]
    print(f"matched {result.matched_count}/{total} ({share:.1%})")
    print(f"unmatched: {unmatched}")
    print(f"inserted: {result.inserted}")
    print(f"duplicates: {result.duplicates}")
    print(f"longest_unmatched_run: {result.longest_unmatched_run}")
    print("boundary_timing: unavailable (no phrase or commit timestamps recorded)")


def rescore(log_path: Path) -> int:
    """Score the ``expected``/``committed`` lines of an earlier run's log."""
    lines: dict[str, list[str]] = {}
    for line in log_path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"(expected|committed) \(\d+\): (.*)$", line)
        if match:
            lines[match.group(1)] = match.group(2).split()
    if set(lines) != {"expected", "committed"}:
        print(f"RESULT FAIL: {log_path} lacks expected/committed lines")
        return 1
    result = score(lines["expected"], lines["committed"])
    print_score(result)
    ok = result.longest_unmatched_run < LOSS_RUN
    print("RESCORE " + ("PASS" if ok else "FAIL") + " (word criterion only)")
    return 0 if ok else 1


def rotation_ages(
    opens: list[float], rotations: list[tuple[float, str]]
) -> list[tuple[float, float, str]]:
    """Pair each rotation with the age of the stream it replaced."""
    paired = []
    for ts, msg in rotations:
        earlier = [o for o in opens if o < ts]
        age = ts - earlier[-1] if earlier else float("nan")
        paired.append((ts, age, msg))
    return paired


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--project")
    mode.add_argument("--rescore", type=Path, metavar="LOG")
    args = parser.parse_args()
    if args.rescore is not None:
        return rescore(args.rescore)

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
    aged = rotation_ages(opens, rotations)
    for ts, age, msg in aged:
        print(f"rotate at_s={ts:.2f} stream_age_s={age:.2f}: {msg}")
    for ts, level, msg in records.lines:
        if level >= logging.WARNING:
            print(f"provider {logging.getLevelName(level)} at_s={ts:.2f}: {msg}")

    print(f"expected ({len(expected)}): {' '.join(expected)}")
    print(f"committed ({len(commits)}): {' | '.join(commits)}")
    print(f"errors: {errors}")
    result = score(expected, commits)
    print_score(result)

    if errors:
        failures.append(f"provider error {errors}")
    ordinary = [
        m for _, age, m in aged if "forced=false" in m and age >= ROTATE_AFTER_S
    ]
    if len(ordinary) != 1:
        failures.append(f"{len(ordinary)} ordinary rotations at stream age >= 270 s")
    if any("forced=true" in msg for _, msg in rotations):
        failures.append("forced rotation occurred")
    if result.longest_unmatched_run >= LOSS_RUN:
        failures.append(f"{result.longest_unmatched_run} consecutive unmatched words")
    print("RESULT " + ("PASS" if not failures else "FAIL: " + "; ".join(failures)))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
