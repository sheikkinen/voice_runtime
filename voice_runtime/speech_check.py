"""Cross-provider speech check (VR-014).

Run as ``python -m voice_runtime.speech_check``. Each provider step runs in a
spawned child under a deadline; see ``--help`` and the README.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import logging
import multiprocessing
import os
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from voice_runtime._speech_check_io import (
    DEFAULT_TEXT,
    FIELDS,
    UsageError,
    _build_parser,
    _load_audio,
    _meta,
    _row,
    _score,
    _selected,
    _table_line,
    _validate,
    _write_out,
)
from voice_runtime._speech_check_io import (
    _wer as _wer,  # re-exported: the tests reach the scorer as speech_check._wer
)

_FRAME_BYTES = 160
_FRAME_S = 0.02
_SETTLE_S = 2.0
_SILENCE_FRAMES = 150  # 3 s of 0xFF at 20 ms frames
_GRACE_S = 1.0
_DEPENDENCY = {
    ("azure", "tts"): "azure.cognitiveservices.speech",
    ("azure", "stt"): "azure.cognitiveservices.speech",
    ("elevenlabs", "tts"): "elevenlabs",
    ("elevenlabs", "stt"): "elevenlabs",
    ("google", "tts"): "google.cloud.texttospeech",
    ("google", "stt"): "google.cloud.speech_v2",
}
_ENV = {
    "azure": ("AZURE_SPEECH_KEY",),
    "elevenlabs": ("ELEVENLABS_API_KEY",),
    "google": ("GOOGLE_SPEECH_PROJECT", "GOOGLE_CLOUD_PROJECT"),
}
logger = logging.getLogger(__name__)


# --- child side: preflight and steps ---------------------------------------------


def _find_spec(name: str) -> Any:
    try:
        return importlib.util.find_spec(name)
    except (ModuleNotFoundError, ValueError):
        return None


def _adc_default() -> None:
    import google.auth

    google.auth.default()


def _skip(reason: str) -> dict[str, Any]:
    return {"status": "SKIP", "reason": reason}


def _preflight(provider: str, kind: str) -> dict[str, Any] | None:
    """Return a SKIP result for a positively identified cause, else None."""
    if _find_spec(f"voice_runtime.providers.{provider}_{kind}") is None:
        return _skip("provider not in this version")
    dependency = _DEPENDENCY[(provider, kind)]
    if _find_spec(dependency) is None:
        return _skip(f"optional dependency missing: {dependency}")
    names = _ENV[provider]
    if not any(os.environ.get(n) for n in names):
        return _skip("missing configuration: " + " or ".join(names))
    if provider == "google":
        try:
            _adc_default()
        except Exception as exc:
            if type(exc).__name__ != "DefaultCredentialsError":
                raise
            return _skip("no ADC: DefaultCredentialsError")
    return None


class _TtsCapture:
    """Capture mixer: ``write_agent`` appends, ``write_caller`` ignores."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.first: float | None = None

    def write_agent(self, chunk: bytes) -> None:
        if self.first is None:
            self.first = time.monotonic()
        self.chunks.append(bytes(chunk))

    def write_caller(self, chunk: bytes) -> None:
        pass


def _tts_step(spec: dict[str, Any], cancel: Any) -> dict[str, Any]:
    from voice_runtime import tts as tts_mod
    from voice_runtime.session import VoiceSession

    provider, recogniser = spec["provider"], spec.get("stt_provider")
    skip = _preflight(provider, "tts")
    if skip is None and recogniser:
        stt_skip = _preflight(recogniser, "stt")
        if stt_skip:
            return {
                **_skip(f"recogniser {recogniser}: {stt_skip['reason']}"),
                "stt_skip": stt_skip["reason"],
            }
    if skip:
        return skip
    engine = tts_mod.create_tts(provider)
    errors: list[str] = []
    engine.on_error = lambda *a: errors.append("on_error: " + " ".join(map(str, a)))
    capture, session, stop_event = _TtsCapture(), VoiceSession(), threading.Event()
    session.set_mixer(capture)
    threading.Thread(
        target=lambda: cancel.wait() and stop_event.set(), daemon=True
    ).start()
    started = time.monotonic()
    result = engine.speak(spec["text"], session, stop_event)
    total = time.monotonic() - started
    audio = b"".join(capture.chunks)
    result = result if isinstance(result, dict) else {}
    reasons = [
        *(["no audio captured"] if not audio else []),
        *([f"result error: {result['error']}"] if result.get("error") else []),
        *errors,
    ]
    return {
        "status": "FAIL" if reasons else "PASS",
        "reason": "; ".join(reasons) or None,
        "first_audio_ms": None if capture.first is None else round((capture.first - started) * 1000),
        "total_ms": round(total * 1000),
        "bytes": len(audio),
        "audio_s": round(len(audio) / 8000, 3),
        "result_keys": sorted(result),
        "audio": audio,
    }  # fmt: skip


class _SttCapture:
    """Records STT callbacks; a raising callback is recorded as an error."""

    def __init__(self) -> None:
        self.commits: list[str] = []
        self.last_commit: float | None = None
        self.recognizing = 0
        self.errors: list[str] = []

    def committed(self, text: str) -> None:
        self.commits.append(text)
        self.last_commit = time.monotonic()

    def on_committed(self, text: str) -> None:
        try:
            self.committed(text)
        except Exception as exc:
            self.errors.append(f"on_committed raised {type(exc).__name__}: {exc}")

    def on_recognizing(self, *args: Any) -> None:
        self.recognizing += 1

    def on_error(self, *args: Any) -> None:
        self.errors.append("on_error: " + " ".join(map(str, args)))


async def _feed(queue: asyncio.Queue, audio: bytes) -> float:
    """Feed frames, then trailing silence, then None; return end of input."""
    end = time.monotonic()
    for i in range(0, len(audio), _FRAME_BYTES):
        queue.put_nowait(audio[i : i + _FRAME_BYTES])
        end = time.monotonic()
        await asyncio.sleep(_FRAME_S)
    for _ in range(_SILENCE_FRAMES):
        queue.put_nowait(b"\xff" * _FRAME_BYTES)
        await asyncio.sleep(_FRAME_S)
    queue.put_nowait(None)
    return end


async def _settle(capture: _SttCapture) -> None:
    fed = time.monotonic()
    while True:
        anchor = max(fed, capture.last_commit or fed)
        wait = anchor + _SETTLE_S - time.monotonic()
        if wait <= 0:
            return
        await asyncio.sleep(wait)


async def _stt_body(engine: Any, capture: _SttCapture, audio: bytes) -> dict[str, Any]:
    queue: asyncio.Queue = asyncio.Queue()
    started = time.monotonic()
    end: float | None = None
    try:
        await engine.start(queue)
    except Exception as exc:
        return {"status": "FAIL", "reason": f"start raised {type(exc).__name__}: {exc}"}
    try:
        end = await _feed(queue, audio)
        await _settle(capture)
    except Exception as exc:
        capture.errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        try:
            await engine.stop()
        except Exception as exc:
            capture.errors.append(f"stop raised {type(exc).__name__}: {exc}")
    reasons = [*(["no commits"] if not capture.commits else []), *capture.errors]
    lag = None
    if end is not None and capture.last_commit is not None:
        lag = round((capture.last_commit - end) * 1000)
    return {
        "status": "FAIL" if reasons else "PASS",
        "reason": "; ".join(reasons) or None,
        "total_ms": round((time.monotonic() - started) * 1000),
        "commits": len(capture.commits),
        "recognizing": capture.recognizing,
        "lag_ms": lag,
        "transcript": " ".join(capture.commits),
    }


async def _stt_run(spec: dict[str, Any], cancel: Any) -> dict[str, Any]:
    from voice_runtime import stt as stt_mod

    engine = stt_mod.create_stt(spec["provider"])
    capture = _SttCapture()
    engine.on_committed = capture.on_committed
    engine.on_recognizing = capture.on_recognizing
    engine.on_error = capture.on_error
    body = asyncio.ensure_future(_stt_body(engine, capture, spec["audio"]))
    while not body.done():
        if cancel.is_set():
            body.cancel()
            break
        await asyncio.wait({body}, timeout=0.02)
    try:
        return await body
    except asyncio.CancelledError:
        return {"status": "FAIL", "reason": "timeout"}


def _stt_step(spec: dict[str, Any], cancel: Any) -> dict[str, Any]:
    skip = _preflight(spec["provider"], "stt")
    return skip or asyncio.run(_stt_run(spec, cancel))


def _child_main(spec: dict[str, Any], conn: Any, cancel: Any) -> None:
    """Run one provider step inside the spawned child; send the result."""
    os.dup2(2, 1)  # stdout belongs to the parent's table or JSON lines
    sys.stdout = sys.stderr
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, force=True)
    step = _tts_step if spec["step"] == "tts" else _stt_step
    try:
        result = step(spec, cancel)
    except Exception as exc:
        logger.exception("speech check step failed")
        result = {"status": "FAIL", "reason": f"{type(exc).__name__}: {exc}"}
    conn.send(result)
    conn.close()


# --- parent side: supervisor, rows and output --------------------------------------


def _reap(proc: Any) -> None:
    proc.join(_GRACE_S)
    if proc.is_alive():
        proc.terminate()
        proc.join(_GRACE_S)
    if proc.is_alive():
        proc.kill()
        proc.join()


def _supervise(
    spec: dict[str, Any],
    timeout: float,
    *,
    _child_target: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """Run one step in a spawned child under a deadline."""
    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    cancel = ctx.Event()
    proc = ctx.Process(
        target=_child_target or _child_main, args=(spec, sender, cancel), daemon=True
    )
    deadline = time.monotonic() + timeout
    proc.start()
    sender.close()
    result: dict[str, Any] = {"status": "FAIL", "reason": "timeout"}
    while (remaining := deadline - time.monotonic()) > 0:
        if receiver.poll(min(remaining, 0.05)):
            try:
                result = receiver.recv()
            except EOFError:
                proc.join(_GRACE_S)
                result = {
                    "status": "FAIL",
                    "reason": f"child exited without a result (exit code {proc.exitcode})",
                }
            break
    else:
        cancel.set()
    _reap(proc)
    receiver.close()
    return result


def _rows(args: argparse.Namespace, run: Callable[[dict[str, Any]], dict[str, Any]]):
    """Yield result rows in order for the selected mode."""
    max_wer = 0.2 if args.max_wer is None else args.max_wer
    for p in _selected(args.provider):
        if args.mode == "stt":
            row = _row(
                "stt",
                p,
                p,
                run({"step": "stt", "provider": p, "audio": args.audio_bytes}),
            )
            _score(row, args.text, max_wer)
            yield row
            continue
        recogniser = (args.stt_provider or p) if args.mode == "roundtrip" else None
        result = run(
            {
                "step": "tts",
                "provider": p,
                "text": args.text,
                "stt_provider": recogniser,
            }
        )
        row = _row("tts", p, None, result)
        if args.out and result.get("audio"):
            try:
                _write_out(args.out, p, result["audio"])
            except OSError as exc:
                row.update(status="FAIL", reason=f"--out write failed: {exc}")
        yield row
        if recogniser is None:
            continue
        if "stt_skip" in result:
            stt = _skip(result["stt_skip"])
        elif row["status"] != "PASS":
            stt = _skip(f"dependency: tts {row['status']}")
        else:
            stt = run({"step": "stt", "provider": recogniser, "audio": result["audio"]})
        stt_row = _row("stt", p, recogniser, stt)
        _score(stt_row, args.text, max_wer)
        yield stt_row


def _parse(argv: list[str] | None) -> argparse.Namespace:
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as exc:
        if exc.code:
            raise UsageError("") from exc
        raise
    _validate(args)
    if args.mode != "stt" and args.text is None:
        args.text = DEFAULT_TEXT
    args.audio_bytes = _load_audio(args.audio) if args.audio else None
    return args


def main(
    argv: list[str] | None = None,
    *,
    _child_target: Callable[..., None] | None = None,
) -> int:
    """CLI entry point; returns the process exit code."""
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    try:
        args = _parse(argv)
    except SystemExit:
        return 0
    except UsageError as exc:
        if str(exc):
            print(f"speech_check: error: {exc}", file=sys.stderr)
        return 2
    meta = _meta(args, __file__)
    print(" ".join(f"{k}={v}" for k, v in meta.items() if k != "type"), file=sys.stderr)
    if args.json:
        print(json.dumps(meta), flush=True)
    else:
        print(
            " | ".join(
                [
                    *FIELDS[:4],
                    "first_audio_ms",
                    "total_ms",
                    "commits",
                    "lag_ms",
                    "wer",
                    "detail",
                ]
            ),
            flush=True,
        )
    statuses = []
    for row in _rows(
        args, lambda spec: _supervise(spec, args.timeout, _child_target=_child_target)
    ):
        statuses.append(row["status"])
        print(
            json.dumps({"type": "result", **row}) if args.json else _table_line(row),
            flush=True,
        )
    if "FAIL" in statuses:
        return 1
    return 0 if "PASS" in statuses else 2


if __name__ == "__main__":
    raise SystemExit(main())
