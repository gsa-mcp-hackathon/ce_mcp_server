"""The `ce-eval` command line, on the real sidecars with no data."""

import json

from ce_eval.cli import main
from tests.eval.conftest import CASES


def test_gold_passes_and_matches_the_frozen_outcomes(monkeypatch, capsys):
    monkeypatch.setenv("CE_META_DATA_DIR", "no-data-here")
    assert main(["gold", "--cases", str(CASES)]) == 0
    out = capsys.readouterr().out
    assert "0 failed, 0 differ from the frozen outcomes" in out


def test_gold_reports_a_frozen_outcome_that_moved(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CE_META_DATA_DIR", "no-data-here")
    for path in CASES.glob("*.yaml"):
        (tmp_path / path.name).write_text(path.read_text())
    frozen = json.loads((CASES / "frozen" / "verdicts.json").read_text())
    frozen["cases"]["rent-by-region"]["verdict"] = "pass"
    (tmp_path / "frozen").mkdir()
    (tmp_path / "frozen" / "verdicts.json").write_text(json.dumps(frozen))
    assert main(["gold", "--cases", str(tmp_path)]) == 1
    assert "DIFF rent-by-region: differs from the frozen outcome in ['verdict']" in (
        capsys.readouterr().out
    )
    assert main(["freeze", "--cases", str(tmp_path)]) == 0
    assert main(["gold", "--cases", str(tmp_path)]) == 0


def test_one_set_is_checked_without_the_frozen_comparison(monkeypatch, capsys):
    monkeypatch.setenv("CE_META_DATA_DIR", "no-data-here")
    assert main(["gold", "--cases", str(CASES), "--set", "out_of_scope", "-v"]) == 0
    out = capsys.readouterr().out
    assert "ok   out_of_scope/eggs-per-week: decline" in out and "9 cases" in out


def test_a_results_file_prints_as_a_report(tmp_path, capsys):
    results = {"model": "m", "judge": None, "repeat": 1, "mode": "run", "scores": []}
    path = tmp_path / "results.json"
    path.write_text(json.dumps(results))
    assert main(["report", str(path), "--cases", str(CASES)]) == 0
    assert "# Evaluation of m" in capsys.readouterr().out
