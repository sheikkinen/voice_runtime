"""VR-014 cross-provider speech check: offline witnesses.

Steps run through the real spawn supervisor with ``tests.vr014_child.entry``
as the test-only child target; fakes live inside the child.
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests import vr014_child
from voice_runtime import speech_check as sc
from voice_runtime.audio import _linear_to_ulaw

pytestmark = pytest.mark.req("VR-014")

DEFAULT_SENTENCE = "Hyvää huomenta, haluaisin varata ajan lääkärille ensi viikolla."
KEYS = {
    "AZURE_SPEECH_KEY": "az-SENTINEL-4242",
    "ELEVENLABS_API_KEY": "el-SENTINEL-4242",
    "GOOGLE_SPEECH_PROJECT": "proj-sentinel-4242",
}
FIELDS = {
    "provider",
    "stt_provider",
    "step",
    "status",
    "reason",
    "first_audio_ms",
    "total_ms",
    "bytes",
    "audio_s",
    "commits",
    "recognizing",
    "lag_ms",
    "wer",
    "result_keys",
    "transcript",
}


def _api(name):
    fn = getattr(sc, name, None)
    assert callable(fn), f"speech_check.{name} missing"
    return fn


def _scenario(tmp_path: Path, *, env=None, unset=(), timing=None, **kw):
    scenario = {
        "record": str(tmp_path / "record.jsonl"),
        "env": {**KEYS, **(env or {})},
        "unset": ["GOOGLE_CLOUD_PROJECT", *unset],
        "timing": {"_FRAME_S": 0.0, "_SETTLE_S": 0.1, **(timing or {})},
    }
    for name in unset:
        scenario["env"].pop(name, None)
    scenario.update(kw)
    return scenario


def _run(capfd, argv, scenario, json_mode=True):
    target = functools.partial(vr014_child.entry, scenario)
    args = [*argv, "--json"] if json_mode else list(argv)
    code = sc.main(args, _child_target=target)
    out, err = capfd.readouterr()
    return code, out, err


def _objects(out: str):
    lines = [line for line in out.splitlines() if line.strip()]
    assert lines, "no stdout"
    return [json.loads(line) for line in lines]


def _rows(out: str):
    return [o for o in _objects(out) if o.get("type") == "result"]


def _events(tmp_path: Path, event: str | None = None):
    path = tmp_path / "record.jsonl"
    if not path.exists():
        return []
    items = [json.loads(line) for line in path.read_text().splitlines()]
    return [e for e in items if event is None or e["event"] == event]


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wav(
    path: Path, tag: int, rate: int, channels: int, bits: int, data: bytes
) -> Path:
    fmt = struct.pack(
        "<HHIIHH",
        tag,
        channels,
        rate,
        rate * channels * bits // 8,
        channels * bits // 8,
        bits,
    )
    body = (
        b"WAVE"
        + b"fmt "
        + struct.pack("<I", len(fmt))
        + fmt
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return path


@pytest.fixture
def ulaw(tmp_path: Path) -> Path:
    path = tmp_path / "in.ulaw"
    path.write_bytes(bytes(range(256)) * 2)
    return path


# --- AC-02 parsing and validation -------------------------------------------

USAGE = [
    (["--mode", "stt"], "--audio"),
    (["--mode", "tts", "--audio", "{ulaw}"], "--audio"),
    (["--mode", "tts", "--stt-provider", "azure"], "--stt-provider"),
    (["--timeout", "0"], "--timeout"),
    (["--timeout", "-1"], "--timeout"),
    (["--timeout", "nan"], "--timeout"),
    (["--timeout", "inf"], "--timeout"),
    (["--max-wer", "-0.1"], "--max-wer"),
    (["--max-wer", "nan"], "--max-wer"),
    (["--mode", "tts", "--max-wer", "0.3"], "--max-wer"),
    (["--mode", "stt", "--audio", "{ulaw}", "--max-wer", "0.3"], "--max-wer"),
    (["--provider", "nuance"], "nuance"),
    (["--text", "  ..  "], "--text"),
    (["--mode", "stt", "--audio", "{ulaw}", "--text", "  ..  "], "--text"),
    (["--mode", "stt", "--audio", "{ulaw}", "--out", "{tmp}"], "--out"),
]


@pytest.mark.parametrize(("argv", "needle"), USAGE)
def test_usage_errors_exit_2_with_reason(argv, needle, ulaw, tmp_path, capfd):
    argv = [a.format(ulaw=ulaw, tmp=tmp_path) for a in argv]
    code, out, err = _run(capfd, argv, _scenario(tmp_path), json_mode=False)
    assert code == 2
    assert needle in err
    assert _events(tmp_path) == []


def test_stt_without_text_has_no_reference(ulaw, tmp_path, capfd):
    scenario = _scenario(tmp_path, stt={"azure": "echo"})
    code, out, _ = _run(
        capfd, ["--provider", "azure", "--mode", "stt", "--audio", str(ulaw)], scenario
    )
    [row] = _rows(out)
    assert (code, row["status"], row["wer"]) == (0, "PASS", None)


def test_provider_selection_dedupes_keeping_first(tmp_path, capfd):
    scenario = _scenario(
        tmp_path, tts={"azure": "emit", "elevenlabs": "emit", "google": "emit"}
    )
    code, out, _ = _run(
        capfd, ["--provider", "google", "--provider", "all", "--mode", "tts"], scenario
    )
    assert [r["provider"] for r in _rows(out)] == ["google", "azure", "elevenlabs"]
    assert code == 0


# --- AC-03 audio input and output -------------------------------------------


def _stt_head(tmp_path, capfd, audio: Path) -> str:
    scenario = _scenario(tmp_path, stt={"azure": "echo"})
    code, out, _ = _run(
        capfd, ["--provider", "azure", "--mode", "stt", "--audio", str(audio)], scenario
    )
    assert [r["status"] for r in _rows(out)] == ["PASS"]
    received = _events(tmp_path, "received")
    assert len(received) == 1
    return received[0]["head"]


def test_raw_ulaw_accepted(ulaw, tmp_path, capfd):
    assert _stt_head(tmp_path, capfd, ulaw).startswith(bytes(range(32)).hex())


def test_ulaw_wav_accepted(tmp_path, capfd):
    data = bytes(range(40, 80))
    audio = _wav(tmp_path / "u.wav", 7, 8000, 1, 8, data)
    assert _stt_head(tmp_path, capfd, audio).startswith(data.hex())


def test_pcm16_wav_converted_with_linear_to_ulaw(tmp_path, capfd):
    samples = [0, 1000, -1000, 32767, -32768]
    audio = _wav(tmp_path / "p.wav", 1, 8000, 1, 16, struct.pack("<5h", *samples))
    expected = bytes(_linear_to_ulaw(s) for s in samples)
    assert _stt_head(tmp_path, capfd, audio).startswith(expected.hex())


@pytest.mark.parametrize(
    "make",
    [
        lambda p: _wav(p, 1, 16000, 1, 16, b"\0\0" * 8),
        lambda p: _wav(p, 1, 8000, 2, 16, b"\0\0" * 8),
        lambda p: _wav(p, 1, 8000, 1, 8, b"\0" * 8),
        lambda p: _wav(p, 3, 8000, 1, 32, b"\0" * 16),
        lambda p: (p.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00"), p)[1],
    ],
    ids=["16khz", "stereo", "pcm8", "float", "truncated"],
)
def test_unsupported_audio_exit_2(make, tmp_path, capfd):
    audio = make(tmp_path / "bad.wav")
    code, _, err = _run(
        capfd,
        ["--mode", "stt", "--audio", str(audio)],
        _scenario(tmp_path),
        json_mode=False,
    )
    assert code == 2
    assert "audio" in err.lower()


def test_out_writes_raw_and_tag7_wav(tmp_path, capfd):
    out_dir = tmp_path / "out"
    scenario = _scenario(tmp_path, tts={"azure": "emit"}, tts_bytes=1600)
    code, out, _ = _run(
        capfd, ["--provider", "azure", "--mode", "tts", "--out", str(out_dir)], scenario
    )
    assert [r["status"] for r in _rows(out)] == ["PASS"]
    assert code == 0
    assert (out_dir / "azure.ulaw").read_bytes() == b"\x7f" * 1600
    wav = (out_dir / "azure.wav").read_bytes()
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    tag, channels, rate, _, _, bits = struct.unpack("<HHIIHH", wav[20:36])
    assert (tag, channels, rate, bits) == (7, 1, 8000, 8)
    assert wav.endswith(b"\x7f" * 1600)
    data_at = wav.index(b"data")
    assert struct.unpack("<I", wav[data_at + 4 : data_at + 8])[0] == 1600


def test_unwritable_out_fails_tts_row(tmp_path, capfd):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    scenario = _scenario(tmp_path, tts={"azure": "emit"})
    code, out, _ = _run(
        capfd, ["--provider", "azure", "--mode", "tts", "--out", str(blocker)], scenario
    )
    [row] = _rows(out)
    assert (code, row["status"]) == (1, "FAIL")
    assert row["reason"]


# --- AC-04 scoring ------------------------------------------------------------


@pytest.mark.parametrize(
    ("hyp", "expected"),
    [
        ("yksi kaksi kolme neljä viisi", 0.0),
        ("Yksi, kaksi KOLME neljä kuusi!", 0.2),
        ("yksi kaksi kolme neljä", 0.2),
        ("yksi kaksi kolme neljä viisi kuusi", 0.2),
        ("", 1.0),
    ],
    ids=["identical", "substitution", "deletion", "insertion", "empty"],
)
def test_wer_distance(hyp, expected):
    assert _api("_wer")("yksi kaksi kolme neljä viisi", hyp) == pytest.approx(expected)


def test_wer_keeps_finnish_letters():
    assert _api("_wer")("pää", "paa") == pytest.approx(1.0)


def _stt_scored(tmp_path, capfd, ulaw, text, commits, *extra):
    scenario = _scenario(tmp_path, stt={"azure": "echo"}, commits=commits)
    argv = [
        "--provider",
        "azure",
        "--mode",
        "stt",
        "--audio",
        str(ulaw),
        "--text",
        text,
        *extra,
    ]
    code, out, _ = _run(capfd, argv, scenario)
    [row] = _rows(out)
    return code, row


def test_commits_joined_form_hypothesis(ulaw, tmp_path, capfd):
    code, row = _stt_scored(tmp_path, capfd, ulaw, "yksi kaksi", ["Yksi.", "kaksi"])
    assert (code, row["status"], row["wer"], row["transcript"]) == (
        0,
        "PASS",
        0.0,
        "Yksi. kaksi",
    )


def test_wer_equal_to_threshold_passes(ulaw, tmp_path, capfd):
    code, row = _stt_scored(
        tmp_path,
        capfd,
        ulaw,
        "yksi kaksi kolme neljä viisi",
        ["yksi kaksi kolme neljä kuusi"],
        "--max-wer",
        "0.2",
    )
    assert (code, row["status"], row["wer"]) == (0, "PASS", pytest.approx(0.2))


def test_wer_above_threshold_fails(ulaw, tmp_path, capfd):
    code, row = _stt_scored(
        tmp_path,
        capfd,
        ulaw,
        "yksi kaksi kolme neljä viisi",
        ["yksi kaksi kolme neljä kuusi"],
        "--max-wer",
        "0.1",
    )
    assert (code, row["status"]) == (1, "FAIL")


# --- AC-05 steps ----------------------------------------------------------------


def _tts(tmp_path, capfd, behaviour, **kw):
    scenario = _scenario(tmp_path, tts={"azure": behaviour}, **kw)
    code, out, _ = _run(capfd, ["--provider", "azure", "--mode", "tts"], scenario)
    [row] = _rows(out)
    return code, row


def test_emitting_fake_is_captured(tmp_path, capfd):
    code, row = _tts(tmp_path, capfd, "emit", tts_bytes=1600)
    assert (code, row["status"], row["bytes"], row["audio_s"]) == (0, "PASS", 1600, 0.2)


def test_unmodified_mock_tts_fails_no_audio(tmp_path, capfd):
    code, row = _tts(tmp_path, capfd, "mock")
    assert (code, row["status"]) == (1, "FAIL")
    assert "audio" in row["reason"]


@pytest.mark.parametrize("behaviour", ["error_result", "on_error"])
def test_tts_failure_causes(behaviour, tmp_path, capfd):
    code, row = _tts(tmp_path, capfd, behaviour, tts_bytes=1600)
    assert (code, row["status"], row["bytes"]) == (1, "FAIL", 1600)


def test_tts_metrics(tmp_path, capfd):
    code, row = _tts(tmp_path, capfd, "emit", tts_bytes=8000, tts_delay_s=0.2)
    assert code == 0
    assert row["first_audio_ms"] >= 200
    assert row["total_ms"] >= row["first_audio_ms"]
    assert row["audio_s"] == 1.0
    assert set(row["result_keys"]) == {"last_spoken", "interrupted"}


def _stt(tmp_path, capfd, ulaw, behaviour, **kw):
    scenario = _scenario(tmp_path, stt={"azure": behaviour}, **kw)
    code, out, _ = _run(
        capfd, ["--provider", "azure", "--mode", "stt", "--audio", str(ulaw)], scenario
    )
    [row] = _rows(out)
    return code, row


@pytest.mark.parametrize("behaviour", ["none", "on_error", "stop_raises"])
def test_stt_failure_causes(behaviour, ulaw, tmp_path, capfd):
    code, row = _stt(tmp_path, capfd, ulaw, behaviour)
    assert (code, row["status"]) == (1, "FAIL")
    assert row["reason"]


@pytest.mark.parametrize("patch", [None, "feed_raises", "commit_raises"])
def test_stop_called_after_successful_start(patch, ulaw, tmp_path, capfd):
    code, row = _stt(tmp_path, capfd, ulaw, "echo", patch=patch)
    assert len(_events(tmp_path, "stop")) == 1
    assert row["status"] == ("PASS" if patch is None else "FAIL")


def test_stt_metrics_order_recognizing_and_lag(ulaw, tmp_path, capfd):
    code, row = _stt(
        tmp_path,
        capfd,
        ulaw,
        "echo",
        commits=["yksi", "kaksi"],
        recognizing=3,
        timing={"_FRAME_S": 0.002, "_SETTLE_S": 0.3},
    )
    assert code == 0
    assert (row["commits"], row["recognizing"], row["transcript"]) == (
        2,
        3,
        "yksi kaksi",
    )
    # 3 s of trailing silence is 150 frames at 2 ms: lag counts from the last original frame
    assert 290 <= row["lag_ms"] <= 1500


def test_settle_window_restarts_on_later_commit(ulaw, tmp_path, capfd):
    code, row = _stt(
        tmp_path,
        capfd,
        ulaw,
        "echo",
        commits=["a", "b", "c"],
        commit_gap_s=0.35,
        timing={"_FRAME_S": 0.0, "_SETTLE_S": 0.5},
    )
    assert (code, row["commits"], row["transcript"]) == (0, 3, "a b c")


def test_roundtrip_with_other_recogniser_names_both(tmp_path, capfd):
    scenario = _scenario(
        tmp_path,
        tts={"azure": "emit"},
        stt={"elevenlabs": "echo"},
        commits=[DEFAULT_SENTENCE],
    )
    code, out, _ = _run(
        capfd, ["--provider", "azure", "--stt-provider", "elevenlabs"], scenario
    )
    tts_row, stt_row = _rows(out)
    assert (tts_row["step"], tts_row["provider"], tts_row["status"]) == (
        "tts",
        "azure",
        "PASS",
    )
    assert (stt_row["step"], stt_row["provider"], stt_row["stt_provider"]) == (
        "stt",
        "azure",
        "elevenlabs",
    )
    assert (stt_row["status"], stt_row["wer"], code) == ("PASS", 0.0, 0)


def test_roundtrip_unavailable_recogniser_skips_both_without_synthesis(tmp_path, capfd):
    scenario = _scenario(
        tmp_path,
        tts={"azure": "emit"},
        stt={"elevenlabs": "echo"},
        unset=["ELEVENLABS_API_KEY"],
    )
    code, out, _ = _run(
        capfd, ["--provider", "azure", "--stt-provider", "elevenlabs"], scenario
    )
    assert [r["status"] for r in _rows(out)] == ["SKIP", "SKIP"]
    assert _events(tmp_path, "speak") == []
    assert code == 2


def test_roundtrip_failed_tts_skips_stt_as_dependency(tmp_path, capfd):
    scenario = _scenario(tmp_path, tts={"azure": "mock"}, stt={"azure": "echo"})
    code, out, _ = _run(capfd, ["--provider", "azure"], scenario)
    tts_row, stt_row = _rows(out)
    assert (tts_row["status"], stt_row["status"], code) == ("FAIL", "SKIP", 1)
    assert stt_row["reason"] == "dependency: tts FAIL"


# --- AC-06 spawn supervisor and deadlines --------------------------------------


def test_large_result_transfers(tmp_path, capfd):
    code, row = _tts(tmp_path, capfd, "emit", tts_bytes=1_500_000)
    assert (code, row["status"], row["bytes"]) == (0, "PASS", 1_500_000)


@pytest.mark.parametrize(
    ("kind", "behaviour", "timing"),
    [
        ("stt", "cancel_coop", {"_SETTLE_S": 30.0}),
        ("tts", "block_hard", {}),
        ("stt", "start_hang", {}),
        ("stt", "stop_block", {}),
        ("tts", "ignore_term", {}),
    ],
)
def test_timeout_is_bounded_and_reaps_child(
    kind, behaviour, timing, ulaw, tmp_path, capfd
):
    scenario = _scenario(tmp_path, timing=timing, **{kind: {"azure": behaviour}})
    argv = ["--provider", "azure", "--mode", kind, "--timeout", "2"]
    if kind == "stt":
        argv += ["--audio", str(ulaw)]
    started = time.monotonic()
    code, out, _ = _run(capfd, argv, scenario)
    elapsed = time.monotonic() - started
    [row] = _rows(out)
    assert (code, row["status"], row["reason"]) == (1, "FAIL", "timeout")
    assert elapsed < 2 + 3 + 0.5
    pids = {e["pid"] for e in _events(tmp_path)}
    assert pids and not any(_alive(pid) for pid in pids)
    if behaviour == "cancel_coop":
        assert len(_events(tmp_path, "stop")) == 1


def test_next_provider_runs_after_timeout(tmp_path, capfd):
    scenario = _scenario(tmp_path, tts={"azure": "block_hard", "elevenlabs": "emit"})
    code, out, _ = _run(
        capfd,
        [
            "--provider",
            "azure",
            "--provider",
            "elevenlabs",
            "--mode",
            "tts",
            "--timeout",
            "2",
        ],
        scenario,
    )
    assert [(r["provider"], r["status"]) for r in _rows(out)] == [
        ("azure", "FAIL"),
        ("elevenlabs", "PASS"),
    ]
    assert code == 1


# --- AC-07 SKIP and FAIL classification ------------------------------------------

ALL_TTS = {"azure": "emit", "elevenlabs": "emit", "google": "emit"}


@pytest.mark.parametrize(
    ("provider", "unset", "needle"),
    [
        ("azure", ["AZURE_SPEECH_KEY"], "AZURE_SPEECH_KEY"),
        ("elevenlabs", ["ELEVENLABS_API_KEY"], "ELEVENLABS_API_KEY"),
        ("google", ["GOOGLE_SPEECH_PROJECT"], "GOOGLE_SPEECH_PROJECT"),
    ],
)
def test_missing_configuration_skips_without_leaking(
    provider, unset, needle, tmp_path, capfd
):
    scenario = _scenario(tmp_path, tts=ALL_TTS, unset=unset)
    code, out, err = _run(capfd, ["--mode", "tts"], scenario)
    rows = {r["provider"]: r for r in _rows(out)}
    assert rows[provider]["status"] == "SKIP"
    assert needle in rows[provider]["reason"]
    for value in KEYS.values():
        assert value not in out and value not in err


def test_missing_optional_dependency_skips(tmp_path, capfd):
    scenario = _scenario(
        tmp_path, tts=ALL_TTS, absent=["azure.cognitiveservices.speech"]
    )
    code, out, _ = _run(capfd, ["--provider", "azure", "--mode", "tts"], scenario)
    [row] = _rows(out)
    assert (row["status"], code) == ("SKIP", 2)
    assert "azure.cognitiveservices.speech" in row["reason"]


@pytest.mark.parametrize("behaviour", ["import_error", "syntax_error"])
def test_broken_present_dependency_fails(behaviour, tmp_path, capfd):
    code, row = _tts(tmp_path, capfd, behaviour)
    assert (code, row["status"]) == (1, "FAIL")


def test_google_absent_from_version_skips_others_run(tmp_path, capfd):
    scenario = _scenario(
        tmp_path,
        tts=ALL_TTS,
        absent=[
            "voice_runtime.providers.google_tts",
            "voice_runtime.providers.google_stt",
        ],
    )
    code, out, _ = _run(capfd, ["--mode", "tts"], scenario)
    rows = {r["provider"]: r for r in _rows(out)}
    assert rows["google"]["status"] == "SKIP"
    assert "not in this version" in rows["google"]["reason"]
    assert rows["azure"]["status"] == rows["elevenlabs"]["status"] == "PASS"
    assert code == 0


def test_google_dispatches_through_same_path(tmp_path, capfd):
    scenario = _scenario(tmp_path, tts={"google": "emit"})
    code, out, _ = _run(capfd, ["--provider", "google", "--mode", "tts"], scenario)
    [row] = _rows(out)
    assert (code, row["status"]) == (0, "PASS")
    assert [e["provider"] for e in _events(tmp_path, "construct")] == ["google"]


def test_no_adc_skips(tmp_path, capfd):
    pytest.importorskip("google.auth", reason="optional google extra not installed")
    scenario = _scenario(tmp_path, tts={"google": "emit"}, adc="missing")
    code, out, _ = _run(capfd, ["--provider", "google", "--mode", "tts"], scenario)
    [row] = _rows(out)
    assert (row["status"], code) == ("SKIP", 2)
    assert "DefaultCredentialsError" in row["reason"]


def test_other_adc_error_fails(tmp_path, capfd):
    scenario = _scenario(tmp_path, tts={"google": "emit"}, adc="error")
    code, out, _ = _run(capfd, ["--provider", "google", "--mode", "tts"], scenario)
    [row] = _rows(out)
    assert (row["status"], code) == ("FAIL", 1)


def test_permission_error_after_construction_fails(tmp_path, capfd):
    code, row = _tts(tmp_path, capfd, "permission")
    assert (code, row["status"]) == (1, "FAIL")


@pytest.mark.parametrize(
    ("tts", "unset", "expected"),
    [
        ({"azure": "emit", "elevenlabs": "emit"}, ["ELEVENLABS_API_KEY"], 0),
        ({"azure": "emit", "elevenlabs": "mock"}, [], 1),
        (
            {"azure": "emit", "elevenlabs": "emit"},
            ["AZURE_SPEECH_KEY", "ELEVENLABS_API_KEY"],
            2,
        ),
    ],
    ids=["pass+skip", "fail", "all-skip"],
)
def test_exit_codes(tts, unset, expected, tmp_path, capfd):
    scenario = _scenario(tmp_path, tts=tts, unset=unset)
    code, out, _ = _run(
        capfd,
        ["--provider", "azure", "--provider", "elevenlabs", "--mode", "tts"],
        scenario,
    )
    assert len(_rows(out)) == 2
    assert code == expected


# --- AC-08 output -------------------------------------------------------------------


def test_json_stdout_is_objects_meta_first_logs_on_stderr(tmp_path, capfd):
    scenario = _scenario(tmp_path, tts={"azure": "log"})
    code, out, err = _run(capfd, ["--provider", "azure", "--mode", "tts"], scenario)
    objects = _objects(out)
    assert all(isinstance(o, dict) for o in objects)
    assert objects[0]["type"] == "meta"
    assert "FAKE-PROVIDER-LOG" in err and "FAKE-PROVIDER-LOG" not in out


def test_result_fields_and_nulls(tmp_path, capfd):
    scenario = _scenario(
        tmp_path,
        tts={"azure": "emit"},
        stt={"azure": "echo"},
        commits=[DEFAULT_SENTENCE],
    )
    code, out, _ = _run(capfd, ["--provider", "azure"], scenario)
    tts_row, stt_row = _rows(out)
    assert set(tts_row) - {"type"} == FIELDS == set(stt_row) - {"type"}
    assert all(
        tts_row[k] is None
        for k in ("commits", "recognizing", "lag_ms", "wer", "transcript")
    )
    assert all(
        stt_row[k] is None
        for k in ("first_audio_ms", "bytes", "audio_s", "result_keys")
    )


def test_table_detail_carries_metrics(tmp_path, capfd):
    scenario = _scenario(
        tmp_path,
        tts={"azure": "emit"},
        stt={"azure": "echo"},
        commits=[DEFAULT_SENTENCE],
        tts_bytes=1600,
        recognizing=2,
    )
    code, out, _ = _run(capfd, ["--provider", "azure"], scenario, json_mode=False)
    assert code == 0
    assert "bytes=1600" in out and "audio_s=0.2" in out and "keys=" in out
    assert "recognizing=2" in out


def test_sha_none_outside_voice_runtime_checkout(tmp_path):
    pkg_root = tmp_path / "copy"
    shutil.copytree(
        Path(sc.__file__).parent,
        pkg_root / "voice_runtime",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in KEYS and k != "GOOGLE_CLOUD_PROJECT"
    }
    env["PYTHONPATH"] = str(pkg_root)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "voice_runtime.speech_check",
            "--json",
            "--provider",
            "azure",
            "--mode",
            "tts",
        ],
        cwd=pkg_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    objects = _objects(proc.stdout)
    meta = objects[0]
    assert meta["type"] == "meta"
    assert meta["file"].startswith(str(pkg_root))
    assert meta["sha"] is None
    assert proc.returncode == 2
