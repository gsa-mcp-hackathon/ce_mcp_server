"""The capture check (`ce_capture.check`): one test per part."""

import copy

from ce_capture.check import GAP_QUESTION, check_record
from ce_capture.records import SCHEMA_VERSION
from tests.capture.conftest import TEXT, node


def record(*nodes, text=TEXT, answers=None, **extra) -> dict:
    nodes = list(nodes) or [node()]
    rec = {
        "schema_version": SCHEMA_VERSION,
        "request_id": "req-test",
        "captured_at": "2026-11-20T00:00:00+00:00",
        "source": {"user_text": text, "session_id": "s", "turn": 1},
        "capture": {
            "path": "client_model",
            "agent": "ce-capture/test",
            "model": None,
            "attempts": 1,
            "status": "captured",
        },
        "sidecars": {"master": None, "tables": [], "vocabulary": None},
        "nodes": copy.deepcopy(nodes),
        "answers": answers
        if answers is not None
        else [{"asks": "average spending per household by region", "node": "n1"}],
        "ambiguities": [],
        "unresolved": [],
        "fixes": [],
        "evidence": [],
        "check": {"passed": True},
    }
    rec.update(extra)
    return rec


def _parts(outcome):
    return [(e["part"], e["field"]) for e in outcome.errors]


def test_a_good_record_passes_with_its_flags(ctx):
    out = check_record(record(), ctx, flags=["statistic_term_differs: x"])
    assert out.passed, out.errors
    assert out.flags == ["statistic_term_differs: x"]  # region is settled; 2025 loaded
    assert [s["where"] for s in out.spans] == [
        "n1.period.text",
        "n1.stated.gap_2025",
        "n1.stated.weighted",
        "answers.0.asks",
    ]
    assert out.spans[0] == {
        "where": "n1.period.text",
        "text": "For 2025",
        "source": "user_text",
        "start": 0,
        "end": 8,
    }


def test_form(ctx):
    rec = record()
    rec["nodes"][0]["statistic"] = "median"
    out = check_record(rec, ctx)
    assert _parts(out)[0][0] == "form"
    assert "nodes.0.estimate.statistic" in out.errors[0]["field"]


def test_references(ctx):
    rec = record(
        node(
            measure={"table": "fmly", "column": "totexpcq"},
            group_by=[{"table": "fmli", "column": "regoin"}],
            filters=[
                {"table": "fmli", "column": "region", "comparison": "eq", "value": "9"}
            ],
        )
    )
    out = check_record(rec, ctx)
    errors = {e["field"]: e for e in out.errors if e["part"] == "references"}
    assert errors["measure.table"]["suggestions"] == ["fmli"]
    assert errors["group_by.0.column"]["suggestions"][0] == "region"
    assert "'9' is not a code" in errors["filters.0.value"]["message"]
    assert errors["filters.0.value"]["suggestions"][0] == "1 (Northeast)"


def test_filter_shapes(ctx):
    flt = {"table": "fmli", "column": "region", "comparison": "in", "value": "1"}
    out = check_record(record(node(filters=[flt])), ctx)
    assert "in takes a list" in out.errors[0]["message"]
    flt = {"table": "fmli", "column": "fincbtax", "comparison": "between", "value": [1]}
    out = check_record(record(node(filters=[flt])), ctx)
    assert "between takes two values" in out.errors[0]["message"]


def test_a_table_with_a_failed_sidecar_check_is_not_covered(ctx):
    from ce_meta.sidecars import CheckResult, Failure, Report

    failure = Failure("table", "ce-interview-mtbi", "something broke")
    ctx.report = Report((CheckResult("x", "y", "failed", (failure,)),))
    rec = record(node(measure={"table": "mtbi", "column": "cost"}, group_by=[]))
    out = check_record(rec, ctx)
    assert "failed a check" in out.errors[0]["message"]


def test_table_reach(ctx):
    rec = record(node(group_by=[{"table": "mtbi", "column": "ucc"}]))
    out = check_record(rec, ctx)
    assert _parts(out) == [("table_reach", "group_by.0")]
    ok = record(
        node(
            measure={"table": "mtbi", "column": "cost"},
            group_by=[
                {"table": "mtbi", "column": "ucc"},
                {"table": "fmli", "column": "region"},
            ],
        )
    )
    assert check_record(ok, ctx).passed


def test_consistency(ctx):
    combine = {
        "id": "n3",
        "kind": "combine",
        "statistic": "ratio",
        "inputs": {"numerator": "n1", "first": "n9"},
    }
    loop_a = {
        "id": "c1",
        "kind": "combine",
        "statistic": "difference",
        "inputs": {"first": "c2", "second": "n1"},
    }
    loop_b = {
        "id": "c2",
        "kind": "combine",
        "statistic": "difference",
        "inputs": {"first": "c1", "second": "n1"},
    }
    rec = record(
        node(),
        node(
            statistic="total", measure=None
        ),  # a second n1, and a total with no measure
        node(id="n4", period={"text": None, "kind": "year", "values": []}),
        combine,
        loop_a,
        loop_b,
        answers=[{"asks": "average spending per household by region", "node": "n7"}],
    )
    messages = [
        e["message"]
        for e in check_record(rec, ctx).errors
        if e["part"] == "consistency"
    ]
    assert "Two nodes have the ID 'n1'." in messages
    assert "A total needs a measure." in messages
    assert any("period has no values" in m for m in messages)
    assert "A ratio takes the inputs numerator and denominator." in messages
    assert "Combine nodes refer to each other in a cycle." in messages
    assert any("Answer 0 names 'n7'" in m for m in messages)


def test_an_empty_record_needs_nodes_or_a_reason(ctx):
    out = check_record(record(answers=[], nodes=[]), ctx)
    assert "no nodes and no reason code" in out.errors[0]["message"]
    rec = record(answers=[], nodes=[])
    rec["capture"]["reason_code"] = "not_statistical"
    assert check_record(rec, ctx).passed


def test_evidence(ctx):
    n = node()
    n["stated"]["weighted"]["evidence"] = "no weights at all"
    out = check_record(record(n), ctx)
    assert _parts(out) == [("evidence", "n1.stated.weighted")]
    assert out.errors[0]["node"] == "n1"
    # a follow-up's evidence may come from the earlier message
    rec = record(text="Now by state.")
    rec["source"]["earlier_text"] = TEXT
    rec["answers"] = []
    out = check_record(rec, ctx)
    assert out.passed and {s["source"] for s in out.spans} == {"earlier_text"}


def test_a_stated_choice_the_record_misses(ctx):
    n = node()
    del n["stated"]["weighted"]
    out = check_record(record(n), ctx)
    assert _parts(out) == [("stated_choices", "stated.weighted")]
    assert "'skip the weights'" in out.errors[0]["message"]
    # a phrase that only confirms a default is not required
    text = "For 2025, as released, average spending per household by region, weighted"
    out = check_record(record(n, text=text), ctx)
    assert out.passed


def test_the_2025_choice_is_asked_when_missing(ctx):
    n = node()
    del n["stated"]["gap_2025"]
    text = (
        "For 2025, what's average spending per household by region? Skip the weights."
    )
    out = check_record(record(n, text=text), ctx)
    assert out.passed and out.missing_gap_choice
    assert out.questions == [GAP_QUESTION]
    # stated on one node, it covers the record
    other = node(id="n2")
    del other["stated"]["gap_2025"]
    out = check_record(record(node(), other, text=TEXT), ctx)
    assert not out.missing_gap_choice
    # a year before the gap needs no choice
    old = node(period={"text": None, "kind": "year", "values": [2024]})
    del old["stated"]["gap_2025"]
    assert not check_record(record(old, text=text), ctx).missing_gap_choice


def test_flags(ctx):
    n = node(
        measure={"table": "fmli", "column": "fsalaryx"},
        period={"text": None, "kind": "year", "values": [2024, 2025]},
    )
    out = check_record(record(n), ctx)
    assert out.flags == ["period_not_loaded: n1 2024", "needs_review: fmli.fsalaryx"]


def test_a_set_level_failure_is_a_flag(ctx):
    from ce_meta.sidecars import CheckResult, Failure, Report

    ctx.report = Report(
        (CheckResult("x", "y", "failed", (Failure("set", None, "bad vocab"),)),)
    )
    out = check_record(record(), ctx)
    assert "sidecar_set_invalid: bad vocab" in out.flags
