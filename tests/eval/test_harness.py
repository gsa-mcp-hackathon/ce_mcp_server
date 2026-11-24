"""The harness end to end, with scripted models on the tiny stored tables.

The oracle does everything right and must score perfectly: that checks the
harness. Each fault breaks one thing, and the matching checker must flag it:
that checks the checkers.
"""

import json

import pytest

from ce_eval import harness, report
from ce_eval.drivers import LoopDriver
from ce_eval.judge import TextJudge
from ce_eval.scripted import FAULTS, Oracle

PROMPT = "You answer with the tools."


def run(tiny, cases, faults=(), **kw):
    driver = LoopDriver(Oracle(cases, tuple(faults)), PROMPT)
    return harness.run_model(tiny.ctx, cases, driver, data_dir=tiny.data_dir, **kw)


def by_case(results) -> dict[str, dict]:
    return {s["case"]: s for s in results["scores"]}


def test_the_oracle_scores_perfectly(tiny, tiny_cases):
    results = run(tiny, tiny_cases)
    scores = by_case(results)
    assert {c: s["failures"] for c, s in scores.items() if s["failures"]} == {}
    assert [s["kind"] for s in results["scores"]] == [
        "figures",
        "figures",
        "block",
        "question",
        "figures",
        "decline",
    ]
    assert all(s["outcome"] == 1.0 and s["status_ok"] for s in results["scores"])
    assert scores["by-region"]["cautions"]["verbatim"] >= 2
    assert scores["member-age"]["block_honest"] is True
    assert scores["income-no-choice"]["question_asked"] is True
    assert scores["by-region"]["guide_first"] is True
    summary = report.summarize(results, report.load_marks())
    assert [m["metric"] for m in summary["marks"] if m["met"] is False] == []


# fault -> the cases it must break, each with a failure it must show
EXPECTED = {
    "drop_cautions": {"by-region": "cautions", "renters": "cautions"},
    "fabricate": {"by-region": "numbers", "member-age": "silent_wrong"},
    "answer_on_block": {"member-age": "silent_wrong"},
    "answer_instead_of_asking": {"income-no-choice": "silent_wrong"},
    "run_unresolved": {"income-no-choice": "tool_order"},
    "no_link": {"renters": "tool_order", "income-as-released": "tool_order"},
    "wrong_measure": {"by-region": "silent_wrong", "income-as-released": "outcome"},
    "invent_weights": {"by-region": "fabricated_stated"},
    "round_figures": {"by-region": "numbers"},
}


def test_every_fault_has_an_expectation():
    assert set(EXPECTED) == set(FAULTS)


@pytest.mark.parametrize("fault", sorted(EXPECTED))
def test_a_fault_is_caught(tiny, tiny_cases, fault):
    scores = by_case(run(tiny, tiny_cases, [fault]))
    for case, failure in EXPECTED[fault].items():
        assert failure in scores[case]["failures"], (case, scores[case]["failures"])


def test_a_fault_leaves_the_other_cases_clean(tiny, tiny_cases):
    scores = by_case(run(tiny, tiny_cases, ["answer_on_block"]))
    assert [c for c, s in scores.items() if s["failures"]] == ["member-age"]
    assert set(scores["member-age"]["failures"]) == {
        "silent_wrong",
        "numbers",
        "block_honesty",
    }


def test_results_are_scored_again_from_their_transcripts(tiny, tiny_cases, tmp_path):
    results = run(tiny, tiny_cases, ["drop_cautions"], records=tmp_path / "records")
    doc = json.loads(json.dumps(results, default=str))  # as read back from a file
    again = harness.rescore(doc, tiny.ctx, tiny_cases, data_dir=tiny.data_dir)

    def strip(scores):
        return [{k: v for k, v in s.items() if k != "seconds"} for s in scores]

    assert strip(again["scores"]) == strip(doc["scores"])


def test_an_interrupted_run_takes_up_where_it_stopped(tiny, tiny_cases, tmp_path):
    kw = {"records": tmp_path / "records", "partial": tmp_path / "partial.jsonl"}
    first = run(tiny, tiny_cases.select(ids=["by-region"]), **kw)
    assert len((tmp_path / "partial.jsonl").read_text().splitlines()) == 1

    class Counting(Oracle):
        seen: list[str] = []

        def chat(self, messages, tools):
            self.seen.append(self._this_turn(messages)[0])
            return super().chat(messages, tools)

    driver = LoopDriver(Counting(tiny_cases), PROMPT)
    full = harness.run_model(tiny.ctx, tiny_cases, driver, data_dir=tiny.data_dir, **kw)
    asked = set(Counting.seen)
    assert not any("by region" in m or "rent" in m for m in asked)  # not run again
    assert len(full["scores"]) == 6 and len(first["scores"]) == 2
    assert {c: s["failures"] for c, s in by_case(full).items() if s["failures"]} == {}


def test_repeats_give_a_consistency_measure(tiny, tiny_cases):
    results = run(tiny, tiny_cases, repeat=2)
    assert len(results["scores"]) == 12
    assert report.measures(results["scores"])["consistency"] == 1.0


def test_a_judge_settles_paraphrased_cautions(tiny, tiny_cases):
    class Paraphraser(Oracle):
        def _present(self, reply: str) -> str:
            text = reply.replace(
                "is not a region; it is reported", "is no region and gets reported"
            )
            return text.replace("stand for one third of", "stand for a third of")

    def scores_with(judge):
        driver = LoopDriver(Paraphraser(tiny_cases), PROMPT)
        results = harness.run_model(
            tiny.ctx, tiny_cases, driver, data_dir=tiny.data_dir, judge=judge
        )
        return by_case(results)["by-region"]

    unjudged = scores_with(None)
    assert unjudged["cautions"]["paraphrase"] >= 1
    assert "cautions" not in unjudged["failures"]
    strict = scores_with(TextJudge(lambda prompt: "DROPPED", "strict"))
    assert strict["cautions"]["missing"] >= 1 and "cautions" in strict["failures"]
    lenient = scores_with(TextJudge(lambda prompt: "KEPT", "lenient"))
    assert lenient["cautions"]["missing"] == 0 and lenient["cautions"]["judged"] >= 1
    # a judge that gives no clear word changes nothing: silence is not "dropped"
    mute = scores_with(TextJudge(lambda prompt: "", "mute"))
    assert mute["cautions"]["missing"] == unjudged["cautions"]["missing"]
    assert "cautions" not in mute["failures"]


def test_a_figure_is_a_silent_wrong_answer_unless_it_is_disclosed(tiny, tiny_cases):
    """The oracle answers a blocked statistic with a figure of its own. With no
    judge that is a silent wrong answer; a judge that finds the figure
    disclosed makes it an open workaround instead."""

    def member_age(judge):
        driver = LoopDriver(Oracle(tiny_cases, ("answer_on_block",)), PROMPT)
        results = harness.run_model(
            tiny.ctx, tiny_cases, driver, data_dir=tiny.data_dir, judge=judge
        )
        return by_case(results)["member-age"]

    assert member_age(None)["silent_wrong"] is True
    silent = member_age(TextJudge(lambda p: "SILENT" if "DISCLOSED" in p else "", "j"))
    assert silent["silent_wrong"] is True and silent["worked_around"] is False
    said = member_age(TextJudge(lambda p: "DISCLOSED" if "SILENT" in p else "", "j"))
    assert said["silent_wrong"] is False and said["worked_around"] is True
    assert "worked_around" in said["failures"]
    assert "silent_wrong" not in said["failures"]


def test_an_alternative_gold_record_is_as_right_as_the_gold(tiny, tiny_cases):
    """Naming a column the gold does not, when the case allows it, scores as
    the gold does; without the alternative it is a wrong figure."""
    from ce_eval.cases import CaseSet, _case
    from tests.eval.conftest import TINY_CASES

    class Other(Oracle):
        def _record(self, case):
            record = super()._record(case)
            if case.id == "by-region":
                record["nodes"][0]["measure"] = {"table": "fmli", "column": "fincbtxm"}
            return record

    def by_region(cases):
        driver = LoopDriver(Other(cases), PROMPT)
        results = harness.run_model(tiny.ctx, cases, driver, data_dir=tiny.data_dir)
        return by_case(results)["by-region"]

    plain = by_region(tiny_cases.select(ids=["by-region"]))
    assert plain["outcome"] == 0.0 and plain["silent_wrong"]
    raw = [dict(c) for c in TINY_CASES if c["id"] == "by-region"]
    other = {"node": "n1", "measure": {"table": "fmli", "column": "fincbtxm"}}
    raw[0]["also"] = [other]
    allowed = CaseSet(tuple(_case(r, "core") for r in raw), (2025,), {})
    score = by_region(allowed)
    assert score["outcome"] == 1.0 and not score["silent_wrong"]
    assert score["detail"]["matched_alternative"] is True


def test_answering_without_a_record_counts_as_not_captured(tiny, tiny_cases):
    from ce_capture.model import ChatReply, FakeModel

    cases = tiny_cases.select(ids=["who-runs-it"])
    model = FakeModel([ChatReply("The Bureau of Labor Statistics runs it.")])
    results = harness.run_model(
        tiny.ctx, cases, LoopDriver(model, PROMPT), data_dir=tiny.data_dir
    )
    score = results["scores"][0]
    assert score["status"] == "not_captured" and score["status_ok"]
    assert score["failures"] == []


def test_invented_stated_choices_are_told_from_supported_ones():
    as_released = {"value": "as_released", "evidence": "as released"}
    gold = {
        "nodes": [
            {"id": "n1", "statistic": "mean", "stated": {"gap_2025": as_released}}
        ]
    }

    def got(text, statistic="mean", **stated):
        node = {"id": "n1", "statistic": statistic, "stated": stated}
        return {"source": {"user_text": text}, "nodes": [node]}

    def choice(value, evidence):
        return {"value": value, "evidence": evidence}

    invent = harness.fabricated_stated
    spending = "Average spending by region in 2025, as released"
    assert invent(got(spending, weighted=choice(True, "average")), gold) == [
        "n1.weighted"
    ]
    said = "Weighted average spending, with the weights, as released"
    assert invent(got(said, weighted=choice(True, "Weighted")), gold) == []
    annual = "Average annual spending, as released"
    assert invent(got(annual, time_basis=choice("year", "annual")), gold) == []
    # a count unit has no effect on a mean; on a count the user's words decide
    per = "Average spending per household, as released"
    assert (
        invent(got(per, count_unit=choice("consumer_unit", "per household")), gold)
        == []
    )
    many = "How many households rented, as released"
    unit = choice("consumer_unit", "households")
    assert invent(got(many, "count", count_unit=unit), gold) == []
    assert invent(got("Renters, as released", "count", count_unit=unit), gold) == [
        "n1.count_unit"
    ]
    assert (
        invent(got(spending, gap_2025=choice("adjusted", "x")), gold) == []
    )  # in gold
    assert invent(None, gold) == []
