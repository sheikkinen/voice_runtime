"""VR-013 round 3 (R-2, AC-15): the AC-12 probe scorer, offline.

Loads the actual probe script; no network, no SDK and no ADC.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PROBE = (
    Path(__file__).resolve().parents[1] / "scripts" / "probe_vr013_google_session.py"
)


@pytest.fixture(scope="module")
def probe():
    spec = importlib.util.spec_from_file_location("probe_vr013_google_session", PROBE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # @dataclass resolves its module here
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


def _api(probe, name: str):
    assert hasattr(probe, name), f"probe has no {name}()"
    return getattr(probe, name)


def _phrases(words: list[str]) -> list[str]:
    return [f"Kohta {w}." for w in words]


@pytest.mark.req("VR-013")
def test_joined_carrier_and_split_across_commits_match(probe):
    result = _api(probe, "score")(
        ["metsä", "norsu", "talo"],
        ["kohtametsä.", "Kohtan", "orsu. Kohta talo!"],
    )
    assert result.matched == [True, True, True]
    assert result.longest_unmatched_run == 0
    assert result.inserted == []


@pytest.mark.req("VR-013")
def test_genuine_substitution_stays_unmatched(probe):
    result = _api(probe, "score")(["sade", "lumi"], ["Kohta fari.", "Kohta lumi."])
    assert result.matched == [False, True]
    assert result.matched_count == 1


@pytest.mark.req("VR-013")
def test_trailing_insertion_is_reported_and_target_still_matches(probe):
    result = _api(probe, "score")(
        ["kilpikonna", "talo"], ["Kohta kilpikonnat.", "Kohta talo."]
    )
    assert result.matched == [True, True]
    assert result.inserted == ["t"]


@pytest.mark.req("VR-013")
@pytest.mark.parametrize(
    ("kept", "run"),
    [
        (["talo", "kissa", "koira", "metsä"], 2),
        (["talo", "kissa", "koira"], 3),
        (["kissa", "koira", "metsä", "järvi", "kukka"], 1),
    ],
)
def test_unmatched_runs_count_in_target_order_including_eof(probe, kept, run):
    targets = ["talo", "kissa", "koira", "metsä", "järvi", "kukka"]
    result = _api(probe, "score")(targets, _phrases(kept))
    assert result.longest_unmatched_run == run
    assert result.matched == [w in kept for w in targets]


@pytest.mark.req("VR-013")
def test_long_repeated_carrier_input_matches_every_target(probe):
    targets = probe.WORDS[:98]
    committed = [
        f"Kohta{w}." if i % 3 == 0 else f"Kohta {w}." for i, w in enumerate(targets)
    ]
    result = _api(probe, "score")(targets, committed)
    assert result.matched_count == 98
    assert result.longest_unmatched_run == 0
    assert result.inserted == []


@pytest.mark.req("VR-013")
def test_duplicated_target_is_reported(probe):
    result = _api(probe, "score")(
        ["talo", "kissa"], ["Kohta talo.", "Kohta kissa.", "kissa"]
    )
    assert result.matched == [True, True]
    assert result.duplicates == ["kissa"]
    assert result.inserted == ["kissa"]


@pytest.mark.req("VR-013")
def test_rotation_age_is_measured_from_the_stream_it_replaced(probe):
    opens = [1.03, 100.0, 380.0]
    rotations = [(380.0, "Google STT stream rotate age_s=280 forced=false")]
    [(ts, age, _)] = _api(probe, "rotation_ages")(opens, rotations)
    assert ts == 380.0
    assert age == pytest.approx(280.0)


@pytest.mark.req("VR-013")
def test_rescore_reads_log_lines_and_gates_on_loss_run(probe, tmp_path, capsys):
    log = tmp_path / "run.log"
    log.write_text(
        "expected (4): talo kissa koira metsä\n"
        "committed (3): kohtatalo kissa kohtametsä\n",
        encoding="utf-8",
    )
    assert _api(probe, "rescore")(log) == 0
    out = capsys.readouterr().out
    assert "matched 3/4" in out
    assert "longest_unmatched_run: 1" in out

    log.write_text(
        "expected (4): talo kissa koira metsä\ncommitted (1): talo\n", encoding="utf-8"
    )
    assert _api(probe, "rescore")(log) == 1
