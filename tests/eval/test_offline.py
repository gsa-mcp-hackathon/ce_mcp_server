"""The gold records with no model: expectations, frozen verdicts, and (slow)
frozen figures on the loaded data."""

import pytest

from ce_eval import offline
from ce_eval.cases import load_cases
from tests.eval.conftest import CASES, DATA


@pytest.fixture(scope="module")
def runs(real_ctx, cases):
    env = offline.make_env(real_ctx, cases.loaded_years)
    return env, offline.check(offline.run_gold(env, cases), env.store)


def test_every_gold_case_meets_its_expectations(runs):
    _, found = runs
    assert {r.case.id: r.problems for r in found if r.problems} == {}


def test_the_outcomes_equal_the_frozen_verdicts(runs):
    """A change to a rule, a default or a sidecar that moves a gold case fails
    here, with no model run. If the change is meant: uv run ce-eval freeze."""
    env, found = runs
    frozen = offline.read_frozen(CASES, offline.VERDICTS_FILE)
    assert frozen is not None, "run: uv run ce-eval freeze"
    assert offline.differences(offline.verdicts_doc(env, found), frozen) == []


def test_the_sets_hold_every_kind_of_answer(runs):
    _, found = runs
    kinds = {offline.snapshot(r)["kind"] for r in found}
    assert {"figures", "block", "question", "decline"} <= kinds


def test_a_rewording_computes_what_its_base_computes(runs):
    _, found = runs
    by_id = {r.case.id: r for r in found}
    for run in found:
        if run.case.rewords:
            base = by_id[run.case.rewords]
            assert (
                offline.snapshot(run)["answered"] == offline.snapshot(base)["answered"]
            )


def test_loaded_years_are_the_declared_ones_whatever_the_registry_says(real_ctx):
    env = offline.make_env(real_ctx, [2025])
    assert env.ctx.year_loaded(2025) and not env.ctx.year_loaded(2024)
    assert not real_ctx.year_loaded(2025)  # the real registry has nothing


def test_outcome_problems_name_what_differs():
    outcome = {
        "verdict": "warn",
        "nodes": [
            {
                "id": "n1",
                "verdict": "warn",
                "method": "do_x",
                "weighted": True,
                "messages": [{"rule": "a"}, {"rule": "b"}],
            }
        ],
    }
    ok = {
        "outcome": {
            "verdict": "warn",
            "n1": {"verdict": "warn", "rules": ["a"], "not_rules": ["c"]},
        }
    }
    assert offline.outcome_problems(ok, outcome) == []
    bad = {
        "outcome": {
            "verdict": "block",
            "n1": {"method": "do_y", "rules": ["z"], "not_rules": ["b"]},
            "n2": {"verdict": "warn"},
        }
    }
    problems = offline.outcome_problems(bad, outcome)
    assert len(problems) == 5
    assert offline.outcome_problems(ok, None) == [
        "no outcome record: the request was not captured"
    ]
    assert offline.outcome_problems({}, None) == []


def test_differences_between_frozen_and_current():
    frozen = {
        "versions": {"rules_version": "1.0"},
        "cases": {"a": {"status": "captured", "v": 1.0}, "gone": {}},
    }
    same = {
        "versions": {"rules_version": "1.0"},
        "cases": {"a": {"status": "captured", "v": 1.0 + 1e-13}, "gone": {}},
    }
    assert offline.differences(same, frozen) == []
    now = {
        "versions": {"rules_version": "1.1"},
        "cases": {"a": {"status": "captured", "v": 2.0}, "new": {}},
    }
    found = offline.differences(now, frozen)
    assert any("versions changed" in d for d in found)
    assert any(d.startswith("a: differs") and "'v'" in d for d in found)
    assert any(d.startswith("new: not in") for d in found)
    assert any(d.startswith("gone: in the frozen") for d in found)


@pytest.mark.slow
@pytest.mark.skipif(not (DATA / "intrvw" / "fmli").exists(), reason="no loaded data")
def test_the_figures_equal_the_frozen_figures(real_ctx):
    """Computed on the loaded 2025 data. A difference means the computation,
    the data or a gold record changed: look, then uv run ce-eval freeze --figures."""
    cases = load_cases(CASES)
    env = offline.make_env(real_ctx, cases.loaded_years, DATA)
    runs = offline.run_gold(env, cases, "run")
    frozen = offline.read_frozen(CASES, offline.FIGURES_FILE)
    assert frozen is not None
    assert offline.differences(offline.figures_doc(env, runs), frozen) == []
