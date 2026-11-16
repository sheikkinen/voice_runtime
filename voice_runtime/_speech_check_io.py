"""Parent-side helpers for the VR-014 speech check: parsing, audio, scoring, output."""

from __future__ import annotations

import argparse
import math
import os
import shutil
import struct
import subprocess
import unicodedata
from importlib import metadata
from pathlib import Path
from typing import Any

PROVIDERS = ("azure", "elevenlabs", "google")
DEFAULT_TEXT = "Hyvää huomenta, haluaisin varata ajan lääkärille ensi viikolla."
FIELDS = (
    "provider", "stt_provider", "step", "status", "reason", "first_audio_ms",
    "total_ms", "bytes", "audio_s", "commits", "recognizing", "lag_ms", "wer",
    "result_keys", "transcript",
)  # fmt: skip
_SDKS = (
    "azure-cognitiveservices-speech",
    "elevenlabs",
    "google-cloud-speech",
    "google-cloud-texttospeech",
)


class UsageError(Exception):
    """Invalid invocation; exit 2 with the message."""


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m voice_runtime.speech_check",
        description="Check TTS/STT providers with synthetic speech.",
    )
    p.add_argument("--provider", action="append", choices=[*PROVIDERS, "all"])
    p.add_argument("--mode", choices=["tts", "stt", "roundtrip"], default="roundtrip")
    p.add_argument("--text", help=f"default (tts/roundtrip): {DEFAULT_TEXT}")
    p.add_argument("--audio", help="stt mode only: .ulaw or 8 kHz mono WAV")
    p.add_argument("--stt-provider", choices=list(PROVIDERS), help="roundtrip only")
    p.add_argument("--out", help="tts/roundtrip: write <provider>.ulaw and .wav")
    p.add_argument("--timeout", type=float, default=30.0, help="per step, seconds")
    p.add_argument("--max-wer", type=float, help="default 0.2; needs a reference")
    p.add_argument("--json", action="store_true", help="JSON lines on stdout")
    return p


def _validate(args: argparse.Namespace) -> None:
    stt = args.mode == "stt"
    checks = [
        (stt and not args.audio, "--audio is required in stt mode"),
        (not stt and args.audio, "--audio is only valid in stt mode"),
        (args.stt_provider and args.mode != "roundtrip", "--stt-provider is roundtrip only"),
        (stt and args.out, "--out is only valid in tts and roundtrip mode"),
        (not (math.isfinite(args.timeout) and args.timeout > 0), "--timeout must be finite and > 0"),
    ]  # fmt: skip
    if args.max_wer is not None:
        has_reference = args.mode == "roundtrip" or (stt and args.text is not None)
        checks += [
            (not has_reference, "--max-wer needs a reference (roundtrip, or stt with --text)"),
            (not (math.isfinite(args.max_wer) and args.max_wer >= 0), "--max-wer must be finite and >= 0"),
        ]  # fmt: skip
    if args.text is not None:
        checks.append(
            (not _normalise(args.text), "--text is empty after normalisation")
        )
    for failed, message in checks:
        if failed:
            raise UsageError(message)


def _selected(names: list[str] | None) -> list[str]:
    out: list[str] = []
    for name in names or ["all"]:
        for p in PROVIDERS if name == "all" else (name,):
            if p not in out:
                out.append(p)
    return out


def _wav_chunks(data: bytes) -> dict[bytes, bytes]:
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise UsageError("unsupported audio: not a .ulaw or WAV file")
    chunks, pos = {}, 12
    while pos < len(data):
        if pos + 8 > len(data):
            raise UsageError("unsupported audio: truncated WAV chunk header")
        cid, size = struct.unpack("<4sI", data[pos : pos + 8])
        if pos + 8 + size > len(data):
            raise UsageError("unsupported audio: truncated WAV chunk")
        chunks[cid] = data[pos + 8 : pos + 8 + size]
        pos += 8 + size + (size & 1)
    if len(chunks.get(b"fmt ", b"")) < 16 or b"data" not in chunks:
        raise UsageError("unsupported audio: WAV needs fmt and data chunks")
    return chunks


def _load_audio(path: str) -> bytes:
    from voice_runtime.audio import _linear_to_ulaw

    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise UsageError(f"cannot read audio: {exc}") from exc
    if path.lower().endswith(".ulaw"):
        audio = data
    else:
        chunks = _wav_chunks(data)
        tag, channels, rate, _, _, bits = struct.unpack("<HHIIHH", chunks[b"fmt "][:16])
        payload = chunks[b"data"]
        if channels != 1 or rate != 8000:
            raise UsageError(f"unsupported audio: {rate} Hz, {channels} channels")
        if (tag, bits) == (7, 8):
            audio = payload
        elif (tag, bits) == (1, 16):
            samples = struct.unpack(
                f"<{len(payload) // 2}h", payload[: len(payload) // 2 * 2]
            )
            audio = bytes(_linear_to_ulaw(s) for s in samples)
        else:
            raise UsageError(f"unsupported audio: format tag {tag}, {bits} bits")
    if not audio:
        raise UsageError("unsupported audio: no samples")
    return audio


def _normalise(text: str) -> str:
    kept = "".join(
        c for c in text.lower() if not unicodedata.category(c).startswith("P")
    )
    return " ".join(kept.split())


def _wer(reference: str, hypothesis: str) -> float:
    ref, hyp = _normalise(reference).split(), _normalise(hypothesis).split()
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / len(ref)


def _write_out(out_dir: str, provider: str, audio: bytes) -> None:
    from voice_runtime.audio import _write_wav_header

    path = Path(out_dir)
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{provider}.ulaw").write_bytes(audio)
    with open(path / f"{provider}.wav", "wb") as f:
        _write_wav_header(f, len(audio))
        f.write(audio)


def _git_sha(package_dir: Path) -> str | None:
    """HEAD of the voice-runtime checkout holding the package, else None."""
    root = package_dir.parent
    git = shutil.which("git")
    try:
        if (
            not git
            or 'name = "voice-runtime"' not in (root / "pyproject.toml").read_text()
        ):
            return None
        # noqa S603: fixed argv, absolute git path, no shell and no user input
        proc = subprocess.run(  # noqa: S603
            [git, "-C", str(root), "rev-parse", "--show-toplevel", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    top = proc.stdout.split()
    if len(top) == 2 and Path(top[0]).resolve() == root.resolve():
        return top[1]
    return None


def _meta(args: argparse.Namespace, file: str) -> dict[str, Any]:
    def version(dist: str) -> str | None:
        try:
            return metadata.version(dist)
        except metadata.PackageNotFoundError:
            return None

    meta: dict[str, Any] = {
        "type": "meta",
        "version": version("voice-runtime"),
        "file": file,
        "sha": _git_sha(Path(file).resolve().parent),
        "sdk": {d: version(d) for d in _SDKS},
        "stt_language_code": os.environ.get("STT_LANGUAGE_CODE"),
    }
    if "google" in [*_selected(args.provider), args.stt_provider]:
        meta["google_project"] = os.environ.get(
            "GOOGLE_SPEECH_PROJECT"
        ) or os.environ.get("GOOGLE_CLOUD_PROJECT")
        meta["google_location"] = os.environ.get("GOOGLE_SPEECH_LOCATION", "eu")
    return meta


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    return f"{value:.3f}" if isinstance(value, float) else str(value)


def _table_line(row: dict[str, Any]) -> str:
    keys = None if row["result_keys"] is None else ",".join(row["result_keys"])
    detail = [f"bytes={_cell(row['bytes'])}", f"audio_s={_cell(row['audio_s'])}",
              f"recognizing={_cell(row['recognizing'])}", f"keys={_cell(keys)}"]  # fmt: skip
    if row["reason"]:
        detail.insert(0, f"reason={row['reason']}")
    if row["transcript"] is not None:
        detail.append(f"transcript={row['transcript']!r}")
    cols = (
        "provider",
        "stt_provider",
        "step",
        "status",
        "first_audio_ms",
        "total_ms",
        "commits",
        "lag_ms",
        "wer",
    )
    return " | ".join([*(_cell(row[c]) for c in cols), " ".join(detail)])


def _row(
    step: str, provider: str, stt_provider: str | None, result: dict[str, Any]
) -> dict[str, Any]:
    row = dict.fromkeys(FIELDS)
    row.update({k: v for k, v in result.items() if k in FIELDS})
    row.update(step=step, provider=provider, stt_provider=stt_provider)
    return row


def _score(row: dict[str, Any], reference: str | None, max_wer: float) -> None:
    if reference is None or row["status"] != "PASS":
        return
    row["wer"] = _wer(reference, row["transcript"] or "")
    if row["wer"] > max_wer:
        row.update(status="FAIL", reason=f"wer {row['wer']:.3f} > {max_wer}")
