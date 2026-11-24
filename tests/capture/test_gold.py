"""The gold set (`ce_capture.gold`): checked offline, and scored with a fake model."""

from ce_capture.gold import DEFAULT_GOLD, check_gold, compare, load_gold, score
from ce_capture.model import ChatReply, FakeModel, call
from tests.capture.conftest import ROOT

GOLD = ROOT / DEFAULT_GOLD


def test_the_gold_set_covers_what_plan_4_asks_for():
    cases = load_gold(GOLD)
    tables = {t for c in cases for t in c["tables"]}
    assert {"fmli", "memi", "mtbi", "itbi"} <= tables
    assert len({t for t in tables if t.startswith("expn_")}) >= 2
    assert any(c.get("follows") for c in cases) and any(c.get("answers") for c in cases)
    statuses = {c["expect"]["status"] for c in cases}
    assert statuses == {"captured", "needs_clarification", "not_captured"}
    assert any(
        a.get("ask_user") for c in cases for a in c["gold"].get("ambiguities", [])
    )


def test_every_gold_case_meets_its_expectations(real_ctx):
    results = check_gold(real_ctx, load_gold(GOLD))
    failed = {r.case: r.problems for r in results if r.problems}
    assert failed == {}


def test_compare_scores_fields_and_stated_choices_apart():
    gold = {
        "status": "captured",
        "nodes": [
            {
                "kind": "estimate",
                "statistic": "mean",
                "measure": {"table": "fmli", "column": "totexpcq"},
                "group_by": [{"table": "fmli", "column": "region"}],
                "filters": [],
                "period": {"kind": "year", "values": [2025]},
                "stated": {
                    "gap_2025": {"value": "as_released"},
                    "weighted": {"value": False},
                },
            }
        ],
    }
    perfect = compare(gold, gold)
    assert (
        perfect["stated"] == 1.0
        and perfect["group_by"] == 1.0
        and perfect["status"] == 1.0
    )
    worse = {
        "status": "needs_clarification",
        "nodes": [
            gold["nodes"][0]
            | {
                "group_by": [],
                "stated": {
                    "gap_2025": {"value": "adjusted"},
                    "time_basis": {"value": "year"},
                },
            }
        ],
    }
    scores = compare(worse, gold)
    assert (
        scores["status"] == 0.0
        and scores["group_by"] == 0.0
        and scores["measure"] == 1.0
    )
    assert scores["stated"] == 0.0 and scores["stated_extra"] == 1


def test_score_with_a_model_that_writes_the_gold_records(real_ctx):
    cases = [
        c
        for c in load_gold(GOLD)
        if c["id"] in ("rent-by-region", "not-a-statistics-request")
    ]

    class Echo(FakeModel):
        def chat(self, messages, tools):
            text = messages[1]["content"]
            gold = next(c["gold"] for c in cases if c["message"] == text)
            self.calls.append((messages, tools))
            return ChatReply(
                None, (call("submit_record", gold),), usage={"total_tokens": 10}
            )

    report = score(real_ctx, Echo([]), cases, repeat=2)
    assert "outcome" not in report["summary"]  # scored only when asked
    assert len(report["runs"]) == 4
    assert report["summary"]["status"] == 1.0 and report["summary"]["measure"] == 1.0
    assert report["summary"]["consistent_cases"] == 2
    assert report["runs"][0]["tokens"] == {"total_tokens": 10}


def test_score_at_the_outcome_level(real_ctx):
    """Naming the other column of a pair is a different record and the same
    outcome: the field score drops, the outcome score does not."""
    from ce_eval.signature import outcome_score

    cases = [c for c in load_gold(GOLD) if c["id"] == "no-2025-choice-answered"]
    cases = [c for c in load_gold(GOLD) if c["id"] == "no-2025-choice"] + cases

    class OtherColumn(FakeModel):
        def chat(self, messages, tools):
            text = messages[1]["content"]
            gold = next(c["gold"] for c in cases if c["message"] == text)
            record = {**gold, "nodes": [
                n | {"measure": {"table": "fmli", "column": "totexppq"}}
                for n in gold["nodes"]]}  # fmt: skip
            return ChatReply(None, (call("submit_record", record),))

    report = score(real_ctx, OtherColumn([]), cases, outcome=outcome_score)
    assert report["summary"]["measure"] == 0.0
    assert report["summary"]["outcome"] == 1.0
    assert report["summary"]["status"] == 1.0
