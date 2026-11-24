"""Derivation (`ce_capture.derive`)."""

import json

import pytest

from ce_capture.derive import derive, measure_kind, pair_columns, year_files
from tests.capture.conftest import ROOT, node, output, submission

SAMPLES = ROOT.parent


def _resolve(pipeline, *nodes, text=None, **extra):
    out = output(*nodes, **extra)
    reply = pipeline.submit(submission(out, **({"text": text} if text else {})))
    assert reply["status"] == "captured", reply
    resolved = pipeline.store.read("resolved", reply["resolved_id"])
    return {n["id"]: n for n in resolved["nodes"]}, resolved


def test_year_files_and_pairs():
    assert year_files(2025) == [
        {"survey_year": 2025, "quarter_codes": ["1x", "2", "3", "4"]},
        {"survey_year": 2026, "quarter_codes": ["1"]},
    ]
    assert pair_columns("totexpcq", "totexppq") == ("totexppq", "totexpcq")
    assert pair_columns("foodpq", "foodcq") == ("foodpq", "foodcq")
    assert pair_columns("abcx4", "abpx4") == ("abpx4", "abcx4")
    assert pair_columns("abcx4", "zzpx4") is None


def test_measure_kinds(ctx):
    kinds = {
        c: measure_kind(t, ctx.column(t, c), ctx)[0]
        for t, c in [
            ("fmli", "totexpcq"),
            ("fmli", "fincbtxm"),
            ("fmli", "fsalaryx"),
            ("fmli", "mystery"),
            ("mtbi", "cost"),
        ]
    }
    assert kinds == {
        "totexpcq": "calendar_pair",
        "fincbtxm": "annual_value",
        "fsalaryx": "unresolved",
        "mystery": "unresolved",
        "cost": "monthly_rows",
    }
    assert measure_kind("fmli", None, ctx) == (
        "unresolved",
        "the column is not in its sidecar",
    )


def test_a_calendar_pair_mean_unweighted(pipeline):
    nodes, resolved = _resolve(pipeline)
    n1 = nodes["n1"]
    assert n1["measure_kind"] == {"value": "calendar_pair", "source": "derived"}
    assert n1["columns"]["value"]["1x"] == ["totexpcq"]
    assert n1["columns"]["value"]["2"] == ["totexppq", "totexpcq"]
    assert n1["weight"] == {"value": None, "source": "stated"}
    assert n1["standard_error"] == {"value": False, "source": "derived"}
    assert n1["unit"] == {
        "measure": "usd_quarterly",
        "result": "usd_annual",
        "source": "derived",
    }
    assert n1["gap_2025"] == {"value": "as_released", "source": "stated"}
    assert n1["geography_level"] == {"value": "region", "source": "derived"}
    assert "weight_route" not in n1 and resolved["defaults_version"] == "1.1"


def test_a_monthly_measure_on_another_table(pipeline):
    rent = node(
        measure={"table": "mtbi", "column": "cost"},
        filters=[
            {"table": "mtbi", "column": "ucc", "comparison": "in", "value": ["210110"]},
            {"table": "fmli", "column": "cutenure", "comparison": "eq", "value": "4"},
        ],
    )
    n1 = _resolve(pipeline, rent)[0]["n1"]
    assert n1["measure_kind"]["value"] == "monthly_rows"
    assert n1["reference_rows"]["value"] == {
        "year_column": "ref_yr",
        "month_column": "ref_mo",
        "years": [2025],
    }
    assert n1["weight_route"]["value"] == [
        {
            "table": "mtbi",
            "parent": "fmli",
            "keys": {"parent": ["newid"], "child": ["newid"]},
            "child_rows_optional": True,
        }
    ]
    # the fmli filter is on another table than the measure's: an interview filter
    assert n1["interview_filters"]["value"] == [
        {"table": "fmli", "column": "cutenure", "comparison": "eq", "value": "4"}
    ]


def test_an_imputed_version_resolves_to_the_mean(pipeline):
    one = node(measure={"table": "fmli", "column": "fincbtx1"}, group_by=[])
    nodes, resolved = _resolve(pipeline, one)
    assert nodes["n1"]["columns"]["value"] == ["fincbtxm"]
    assert nodes["n1"]["imputation"] == {
        "value": "five_versions_averaged",
        "source": "defaulted",
    }
    assert any(f.startswith("imputed_version_averaged: n1") for f in resolved["flags"])
    assert any(f.startswith("annual_value_mean: n1") for f in resolved["flags"])


def test_a_stated_version_is_kept(pipeline):
    text = (
        "For 2025, as released, average income per household. "
        "Use the first imputed version. Skip the weights."
    )
    n = node(measure={"table": "fmli", "column": "fincbtxm"}, group_by=[])
    n["stated"]["imputation"] = {
        "value": "version_1",
        "evidence": "Use the first imputed version",
    }
    answers = [{"asks": "average income per household", "node": "n1"}]
    nodes, _ = _resolve(pipeline, n, text=text, answers=answers)
    assert nodes["n1"]["columns"]["value"] == ["fincbtx1"]
    assert nodes["n1"]["imputation"] == {"value": "version_1", "source": "stated"}


def test_an_unverified_unit_is_unresolved_with_a_flag(pipeline):
    n = node(measure={"table": "fmli", "column": "fsalaryx"}, group_by=[])
    nodes, resolved = _resolve(pipeline, n)
    assert nodes["n1"]["measure_kind"]["value"] == "unresolved"
    assert nodes["n1"]["unit"]["result"] is None
    assert resolved["flags"] == [
        "measure_kind_unresolved: n1 fmli.fsalaryx: its ce:unit is VERIFY"
    ]


def test_counts(pipeline):
    text = "For 2025, as released: how many interviews by region, weighted?"
    count = node(statistic="count", measure=None)
    count["stated"] = {
        "gap_2025": {"value": "as_released", "evidence": "as released"},
        "count_unit": {"value": "interview", "evidence": "how many interviews"},
    }
    answers = [{"asks": "how many interviews by region", "node": "n1"}]
    n1 = _resolve(pipeline, count, text=text, answers=answers)[0]["n1"]
    assert n1["count_method"] == {"value": "weighted_population", "source": "derived"}
    assert n1["count_unit"] == {"value": "interview", "source": "stated"}
    assert n1["weight"] == {"value": "finlwt21", "source": "defaulted"}
    assert n1["standard_error"] == {"value": True, "source": "defaulted"}
    assert "time_basis" not in n1 and "missing_codes" not in n1


def test_a_record_level_2025_choice_reaches_every_node(pipeline):
    other = node(id="n2", statistic="count", measure=None)
    other["stated"] = {}
    nodes, _ = _resolve(pipeline, node(), other)
    assert nodes["n2"]["gap_2025"] == {"value": "as_released", "source": "stated"}


def test_combine_nodes(pipeline):
    text = (
        "For 2025, as released, "
        "what's average spending per household by region relative to income?"
        " Skip the weights."
    )
    n2 = node(id="n2", measure={"table": "fmli", "column": "fincbtxm"})
    ratio = {
        "id": "n3",
        "kind": "combine",
        "statistic": "ratio",
        "inputs": {"numerator": "n1", "denominator": "n2"},
    }
    gap = {
        "id": "n4",
        "kind": "combine",
        "statistic": "difference",
        "inputs": {"first": "n3", "second": "n3"},
    }
    nodes, _ = _resolve(pipeline, node(), n2, ratio, gap, text=text)
    assert nodes["n3"]["inputs"] == {
        "numerator": {"node": "n1", "unit": "usd_annual"},
        "denominator": {"node": "n2", "unit": "usd_annual"},
    }
    assert nodes["n3"]["unit"] == {"result": "proportion", "source": "derived"}
    assert nodes["n3"]["standard_error"]["value"] is False  # the inputs are unweighted
    assert nodes["n4"]["unit"]["result"] == "proportion"  # a combine of a combine


def test_a_quarterly_input_is_converted(pipeline):
    text = (
        "For 2025, as released, "
        "what's average spending per household by region "
        "relative to quarterly spending?"
        " Skip the weights."
    )
    q = node(id="n2")
    q["stated"]["time_basis"] = {"value": "quarter", "evidence": "quarterly"}
    ratio = {
        "id": "n3",
        "kind": "combine",
        "statistic": "ratio",
        "inputs": {"numerator": "n1", "denominator": "n2"},
    }
    nodes, _ = _resolve(pipeline, node(), q, ratio, text=text)
    assert (
        nodes["n3"]["conversion"]["value"]
        == "denominator from usd_quarterly to usd_annual"
    )
    assert nodes["n3"]["unit"]["result"] == "proportion"


def test_part_year_and_unstated_periods(pipeline):
    text = (
        "For Q2 2025, as released, "
        "what's average spending per household by region? Skip the weights."
    )
    q = node(period={"text": "For Q2 2025", "kind": "quarter", "values": ["2025Q2"]})
    nodes, resolved = _resolve(pipeline, q, text=text)
    assert nodes["n1"]["within_year"] == {
        "value": {"quarters": ["2025Q2"]},
        "source": "stated",
    }
    assert resolved["flags"] == ["part_year_period: n1 asks for 2025Q2"]
    none = node(period={"text": None, "kind": "not_stated", "values": []})
    nodes, _ = _resolve(pipeline, none)
    assert nodes["n1"]["year_sets"][0]["source"] == "defaulted"


def test_rerunning_derivation_gives_a_new_resolved_record(pipeline):
    reply = pipeline.submit(submission())
    record = pipeline.store.read("request", reply["request_id"])
    again = derive(record, pipeline.ctx, "res-x-002", "2026-11-21T00:00:00+00:00")
    first = pipeline.store.read("resolved", reply["resolved_id"])
    assert (
        again["nodes"] == first["nodes"]
        and again["resolved_id"] != first["resolved_id"]
    )


@pytest.mark.skipif(
    not (SAMPLES / "sample-request-record.json").exists(), reason="no samples"
)
def test_the_sample_pair_is_reproduced(real_ctx, tmp_path):
    """The hand-written sample request resolves to the hand-written sample."""
    from ce_capture.pipeline import CapturePipeline
    from ce_capture.storage import JsonFileStore

    sample = json.loads((SAMPLES / "sample-request-record.json").read_text())
    expected = json.loads((SAMPLES / "sample-resolved-record.json").read_text())
    part = {k: sample[k] for k in ("nodes", "answers", "ambiguities", "unresolved")}
    pipeline = CapturePipeline(real_ctx, JsonFileStore(tmp_path))
    reply = pipeline.submit(submission(part, text=sample["source"]["user_text"]))
    assert reply["status"] == "captured", reply
    got = pipeline.store.read("resolved", reply["resolved_id"])
    for key in ("nodes", "flags", "defaults_version"):
        assert got[key] == expected[key], key
