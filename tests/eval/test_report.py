"""The measures, the pass marks and the report text."""

from ce_eval import report
from ce_eval.cases import Nuance


def score(case="a", set_="core", **more) -> dict:
    base = {
        "case": case,
        "set": set_,
        "nuance": ["gap_2025"],
        "run": 0,
        "model": "m",
        "gold_kind": "figures",
        "kind": "figures",
        "status_ok": True,
        "outcome": 1.0,
        "stated_extra": 0,
        "asked_ok": None,
        "first_pass": True,
        "attempts": 1,
        "lookups": 4,
        "faithful_numbers": True,
        "block_honest": None,
        "question_asked": None,
        "cautions": {
            "total": 2,
            "verbatim": 1,
            "paraphrase": 1,
            "missing": 0,
            "judged": 0,
        },
        "cautions_optional": False,
        "order_ok": True,
        "guide_first": True,
        "silent_wrong": False,
        "missed": False,
        "tokens": 100,
        "seconds": 2.0,
        "error": None,
        "failures": [],
        "signature": "captured:x",
    }
    return base | more


def test_measures_over_turn_scores():
    scores = [
        score("a"),
        score(
            "b",
            set_="traps",
            silent_wrong=True,
            faithful_numbers=False,
            outcome=0.5,
            lookups=8,
            failures=["silent_wrong", "numbers"],
            block_honest=False,
            cautions={
                "total": 2,
                "verbatim": 0,
                "paraphrase": 0,
                "missing": 2,
                "judged": 0,
            },
        ),
        score(
            "c",
            cautions_optional=True,
            cautions={
                "total": 4,
                "verbatim": 0,
                "paraphrase": 0,
                "missing": 4,
                "judged": 0,
            },
        ),
    ]
    m = report.measures(scores)
    assert m["turns"] == 3 and m["cases"] == 3
    assert m["silent_wrong_rate"] == 1 / 3 and m["number_fidelity"] == 2 / 3
    assert m["outcome_equivalence"] == 2.5 / 3
    assert m["caution_retention"] == 0.5  # 2 of 4: the optional case is left out
    assert m["caution_verbatim"] == 0.25 and m["cautions_paraphrased_unjudged"] == 1
    assert m["block_honesty"] == 0.0 and m["question_asked"] is None
    assert m["median_lookups"] == 4 and m["failed_turns"] == 1
    assert m["consistency"] is None
    assert report.measures([]) == {"turns": 0}


def test_consistency_needs_repeats():
    same = [score("a", run=0), score("a", run=1), score("b", run=0), score("b", run=1)]
    assert report.consistency(same) == 1.0
    same[3] = score("b", run=1, signature="needs_clarification:")
    assert report.consistency(same) == 0.5


def test_pass_marks_by_scope():
    scores = [score("a"), score("b", set_="traps", silent_wrong=True)]
    marks = [
        {"metric": "silent_wrong_rate", "scope": "traps", "max": 0.0},
        {"metric": "silent_wrong_rate", "scope": "core", "max": 0.0},
        {"metric": "status_accuracy", "scope": "all", "min": 0.95},
        {"metric": "consistency", "scope": "all", "min": 0.9},
    ]
    checked = report.check_marks(scores, marks)
    assert [m["met"] for m in checked] == [False, True, True, None]
    assert checked[0]["value"] == 1.0


def test_the_report_reads_by_set_and_by_nuance():
    results = {
        "model": "m",
        "judge": None,
        "repeat": 1,
        "mode": "run",
        "scores": [
            score("a"),
            score(
                "b",
                set_="traps",
                nuance=["region_c"],
                silent_wrong=True,
                failures=["silent_wrong"],
                gold_kind="block",
            ),
        ],
    }
    summary = report.summarize(results, report.load_marks())
    assert summary["failures"] == {"silent_wrong": 1}
    assert set(summary["by_set"]) == {"core", "traps"}
    assert summary["by_nuance"]["region_c"]["silent_wrong_rate"] == 1.0
    nuances = {"region_c": Nuance("region_c", "Region code C from 2025", "", "")}
    text = report.markdown(results, summary, nuances)
    assert "# Evaluation of m" in text and "## Failed turns (1, in 1 cases)" in text
    assert "| Region code C from 2025 | 1 | 100% |" in text
    assert "| silent_wrong_rate | traps | 100% | at most 0% | NOT MET |" in text
    assert "block → figures" in text and "tool description" in text
    assert "| consistency | all | n/a | at least 90% | not measured |" in text


def test_failed_cases_count_the_runs_each_failed_in():
    scores = [
        score("a", run=0, failures=["cautions"]),
        score("a", run=1),
        score("a", run=2, failures=["cautions", "numbers"], kind="block"),
        score("b", run=0),
    ]
    found = report.failed_cases(scores)
    assert len(found) == 1 and found[0]["failed"] == 2
    assert found[0]["failures"] == {"cautions": 2, "numbers": 1}
    assert found[0]["kinds"] == ["figures", "block"]
    results = {
        "model": "m",
        "judge": None,
        "repeat": 3,
        "mode": "run",
        "scores": scores,
    }
    text = report.markdown(results, report.summarize(results, []))
    assert "| a | core | gap_2025 | 2 of 3 | cautions (2), numbers (1) |" in text
