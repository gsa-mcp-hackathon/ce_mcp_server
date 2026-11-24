"""The interview frame and the measures, on the tiny stored tables.

Expected values are worked out by hand from the rows in conftest.py. Survey
year 2025 is the files 2025 1x, 2, 3, 4 and 2026 1; interview 32 has no months
in scope; the 2025 `1` file (interviews 11 and 99) belongs to 2024 and is
never read. Calendar-pair values per interview, x = cq / pq + cq / pq:

| newid | region | x | w | mo_scope |
| --- | --- | --- | --- | --- |
| 11 | 1 | 600 | 100 | 2 |
| 12 | 2 | 300 | 200 | 1 |
| 21 | 1 | 1500 | 100 | 3 |
| 22 | 2 | 600 | 200 | 3 |
| 23 | C | 300 | 150 | 3 |
| 31 | 1 | 1200 | 100 | 3 |
| 41 | 2 | 900 | 200 | 3 |
| 51 | C | 400 | 150 | 2 |

Population Σ(w·mo_scope/12) = 2950 / 12.
"""

from __future__ import annotations

import pytest

from ce_measures import join
from ce_measures.combine import difference, ratio
from ce_measures.data import open_table
from ce_measures.errors import DataMissing
from ce_measures.measures.base import clean
from ce_measures.models import Estimate, GroupEstimate
from tests.measures.conftest import captured, estimate, node_of

POP = 2950 / 12
RENT = [{"table": "mtbi", "column": "ucc", "comparison": "in", "value": ["210110"]}]
UNWEIGHTED = {"weighted": {"value": False, "evidence": "unweighted"}}
AS_RELEASED = {"gap_2025": {"value": "as_released", "evidence": "as released"}}


def _evidence(node: dict) -> list[str]:
    words = [c["evidence"] for c in (node.get("stated") or {}).values()]
    if node.get("period"):
        words.append(node["period"]["text"])
    return words


def run(tiny, *nodes: dict, mode: str = "run") -> dict[str, dict]:
    """Capture the nodes through Plan 4's pipeline and process them; each node's
    outcome entry by id."""
    answers = [{"asks": f"asks for {n['id']}", "node": n["id"]} for n in nodes]
    words = [w for n in nodes for w in _evidence(n)] + [a["asks"] for a in answers]
    text = "Test: " + "; ".join(dict.fromkeys(words)) + "."
    output = {
        "nodes": list(nodes),
        "answers": answers,
        "ambiguities": [],
        "unresolved": [],
    }
    rid = captured(tiny.ctx, tiny.store, text, output)
    fn = tiny.processor.run if mode == "run" else tiny.processor.evaluate
    record, _ = fn(rid)
    return {n.id: n.model_dump(by_alias=True) for n in record.nodes}


def by_group(entry: dict, column: str = "region") -> dict:
    return {g["group"].get(column): g["value"] for g in entry["result"]["groups"]}


# ── the frame ────────────────────────────────────────────────────────────────


def _sources(tiny, *tables):
    return {t: open_table(tiny.data_dir, tiny.sidecars, t) for t in tables}


def test_open_table_refuses_a_table_with_no_sidecar_or_no_rows(tiny):
    assert "read_parquet" in open_table(tiny.data_dir, tiny.sidecars, "fmli")
    with pytest.raises(DataMissing, match="No sidecar"):
        open_table(tiny.data_dir, tiny.sidecars, "nosuch")
    with pytest.raises(DataMissing):
        open_table(None, tiny.sidecars, "fmli")


def test_no_value_from_the_record_is_written_into_the_sql(tiny):
    node = node_of(
        tiny.ctx,
        estimate(
            measure={"table": "mtbi", "column": "cost"},
            filters=[
                *RENT,
                {
                    "table": "fmli",
                    "column": "cutenure",
                    "comparison": "eq",
                    "value": "TENURE-4",
                },
            ],
        ),
    )
    query = join.build_sql(node, {"fmli": "F", "mtbi": "M"}, tiny.sidecars, base="mtbi")
    for value in ("210110", "TENURE-4", "2025", "2026", "1x"):
        assert value not in query.sql
    assert "210110" in query.params and "TENURE-4" in query.params
    assert query.sql.count("?") == len(query.params)


def test_the_fmli_frame_has_one_row_per_interview_of_the_year_set(tiny):
    node = node_of(tiny.ctx, estimate())
    frame = join.interview_frame(node, _sources(tiny, "fmli"), tiny.sidecars)
    rows = {r["newid"]: r for r in frame.table.to_pylist()}
    assert sorted(rows) == ["11", "12", "21", "22", "23", "31", "32", "41", "51"]
    assert {k: rows[k]["x"] for k in ("11", "21", "51")} == {
        "11": 600,
        "21": 1500,
        "51": 400,
    }
    assert rows["11"]["set_year"] == 2025 and rows["11"]["mo_scope"] == 2
    cleaned = clean(frame)
    assert cleaned.table.num_rows == 8 and cleaned.removed_null_mo_scope == 1


def test_a_monthly_table_is_summed_to_the_interview_with_zero_for_no_rows(tiny):
    node = node_of(
        tiny.ctx, estimate(measure={"table": "mtbi", "column": "cost"}, filters=RENT)
    )
    frame = join.interview_frame(
        node, _sources(tiny, "fmli", "mtbi"), tiny.sidecars, base="mtbi"
    )
    x = {r["newid"]: r["x"] for r in frame.table.to_pylist()}
    # 11: the 2024 reference row is left out; 22: the parent record is removed
    assert x == {
        "11": 900,
        "12": 0,
        "21": 2000,
        "22": 800,
        "23": 0,
        "31": 700,
        "32": 0,
        "41": 0,
        "51": 0,
    }
    assert frame.removed_missing == 1


def test_a_grouping_on_the_measure_table_puts_every_interview_in_every_group(tiny):
    node = node_of(
        tiny.ctx,
        estimate(
            measure={"table": "mtbi", "column": "cost"},
            group_by=[{"table": "mtbi", "column": "ucc"}],
        ),
    )
    frame = join.interview_frame(
        node, _sources(tiny, "fmli", "mtbi"), tiny.sidecars, base="mtbi"
    )
    rows = frame.table.to_pylist()
    assert len(rows) == 9 * 2  # nine interviews, two item codes
    groceries = {r["newid"]: r["x"] for r in rows if r["g0"] == "790210"}
    assert groceries["21"] == 300 and groceries["41"] == -50 and groceries["12"] == 0


def test_an_interview_filter_keeps_interviews_with_a_matching_row(tiny):
    asked = estimate(
        statistic="count",
        measure=None,
        group_by=[],
        filters=[{"table": "memi", "column": "age", "comparison": "ge", "value": 65}],
    )
    node = node_of(tiny.ctx, asked)
    frame = join.interview_frame(node, _sources(tiny, "fmli", "memi"), tiny.sidecars)
    assert sorted(r["newid"] for r in frame.table.to_pylist()) == [
        "11",
        "21",
        "31",
        "32",
    ]


def test_a_member_frame_has_one_row_per_member_interview(tiny):
    asked = estimate(
        statistic="count",
        measure=None,
        group_by=[{"table": "memi", "column": "sex"}],
        stated={"count_unit": {"value": "member", "evidence": "people"}},
    )
    node = node_of(tiny.ctx, asked)
    frame = join.interview_frame(
        node, _sources(tiny, "fmli", "memi"), tiny.sidecars, base="memi"
    )
    assert frame.member_rows and frame.table.num_rows == 10


def test_the_adjusted_form_rescales_months_and_amounts(tiny):
    asked = estimate(
        measure={"table": "mtbi", "column": "cost"},
        filters=RENT,
        stated={"gap_2025": {"value": "adjusted", "evidence": "adjusted"}},
    )
    node = node_of(tiny.ctx, asked)
    frame = join.interview_frame(
        node, _sources(tiny, "fmli", "mtbi"), tiny.sidecars, base="mtbi"
    )
    rows = {r["newid"]: r for r in frame.table.to_pylist()}
    assert rows["31"]["mo_scope"] == 3.5  # interviewed in August 2025
    assert rows["41"]["mo_scope"] == 5.5  # December 2025
    assert rows["21"]["mo_scope"] == 3  # June: as stored
    assert rows["31"]["x"] == 700 * 3  # an August amount is tripled


# ── means ────────────────────────────────────────────────────────────────────


def test_calendar_pair_mean_by_region(tiny):
    out = run(tiny, estimate())
    means = by_group(out["n1"])
    assert means["1"] == pytest.approx(330000 / (800 / 12))  # 4950
    assert means["1"] == pytest.approx(4950)
    assert means["2"] == pytest.approx(360000 / (1400 / 12))
    assert means["C"] == pytest.approx(1680)
    result = out["n1"]["result"]
    assert result["removed_null_mo_scope"] == 1 and result["unit"] == "usd_annual"
    assert out["n1"]["method"] == "do_calendar_pair_mean"


def test_calendar_pair_mean_unweighted_and_per_quarter(tiny):
    out = run(
        tiny,
        estimate(
            group_by=[],
            stated={
                **UNWEIGHTED,
                **AS_RELEASED,
                "time_basis": {"value": "quarter", "evidence": "per quarter"},
            },
        ),
    )
    x = [600, 300, 1500, 600, 300, 1200, 900, 400]
    months = [2, 1, 3, 3, 3, 3, 3, 2]
    value = out["n1"]["result"]["groups"][0]["value"]
    assert value == pytest.approx(sum(x) / (sum(months) / 12) / 4)
    assert out["n1"]["weighted"] is False


def test_annual_value_mean_uses_months_in_scope_on_both_sides(tiny):
    out = run(tiny, estimate(measure={"table": "fmli", "column": "fincbtxm"}))
    means = by_group(out["n1"])
    assert means["1"] == pytest.approx((50000 * 200 + 50000 * 300 + 52000 * 300) / 800)
    assert out["n1"]["method"] == "do_annual_value_mean"


def test_interview_value_mean_keeps_the_unit(tiny):
    out = run(
        tiny, estimate(measure={"table": "fmli", "column": "mo_scope"}, group_by=[])
    )
    assert out["n1"]["method"] == "do_interview_value_mean"
    group = out["n1"]["result"]["groups"][0]
    months = [2, 1, 3, 3, 3, 3, 3, 2]
    w = [100, 200, 100, 200, 150, 100, 200, 150]
    num = sum(m * m * wi for m, wi in zip(months, w, strict=True))
    assert group["value"] == pytest.approx(
        num / sum(m * wi for m, wi in zip(months, w, strict=True))
    )
    assert out["n1"]["result"]["unit"] == "months"


def test_a_mean_of_a_count_valued_column_keeps_its_decimals(tiny):
    """A mean of 2.47 months, or 1.93 vehicles, is not shown as 2."""
    from ce_measures.reply import _number

    assert _number(1.93, "count", whole=False) == "1.93"
    assert _number(1720.0, "count") == "1,720"
    assert _number(88010.4, "usd_annual", whole=False) == "$88,010"


def test_monthly_rows_mean_counts_interviews_with_no_rows_as_zero(tiny):
    out = run(tiny, estimate(measure={"table": "mtbi", "column": "cost"}, filters=RENT))
    means = by_group(out["n1"])
    assert means["1"] == pytest.approx(
        (900 * 100 + 2000 * 100 + 700 * 100) / (800 / 12)
    )
    assert means["C"] == 0
    assert out["n1"]["method"] == "do_monthly_rows_mean"
    assert out["n1"]["result"]["removed_missing"] == 1  # the parent record


def test_the_five_imputed_versions_of_itii_are_averaged(tiny):
    asked = estimate(
        measure={"table": "itii", "column": "value"},
        group_by=[],
        filters=[
            {"table": "itii", "column": "ucc", "comparison": "eq", "value": "900000"}
        ],
    )
    out = run(tiny, asked)
    group = out["n1"]["result"]["groups"][0]
    assert group["value"] == pytest.approx((100 * 1030 + 200 * 500) / POP)
    assert group["standard_error"] is not None
    assert "five imputed versions" in out["n1"]["result"]["standard_error_method"]


# ── counts and totals ────────────────────────────────────────────────────────

COUNT = dict(statistic="count", measure=None)


def test_the_four_counts(tiny):
    out = run(
        tiny,
        estimate(id="n1", **COUNT, group_by=[]),
        estimate(id="n2", **COUNT, group_by=[], stated={**UNWEIGHTED, **AS_RELEASED}),
        estimate(
            id="n3",
            **COUNT,
            group_by=[],
            stated={
                **UNWEIGHTED,
                **AS_RELEASED,
                "count_unit": {"value": "interview", "evidence": "interviews"},
            },
        ),
        estimate(
            id="n4",
            **COUNT,
            group_by=[{"table": "memi", "column": "sex"}],
            stated={
                **UNWEIGHTED,
                **AS_RELEASED,
                "count_unit": {"value": "member", "evidence": "people"},
            },
        ),
    )
    value = lambda n: out[n]["result"]["groups"][0]["value"]  # noqa: E731
    assert value("n1") == pytest.approx(POP)  # consumer units in the population
    assert value("n2") == 4  # A, B, C, D: a unit with four interviews counts once
    assert value("n3") == 9  # interviews, 32 included: months play no part
    assert by_group(out["n4"], "sex") == {"1": 2, "2": 3}  # A1 counts once
    assert [out[n]["method"] for n in ("n1", "n2", "n3", "n4")] == [
        "do_weighted_count",
        "do_distinct_count",
        "do_row_count",
        "do_distinct_member_count",
    ]


def test_a_count_of_households_with_an_older_member(tiny):
    asked = estimate(
        **COUNT,
        group_by=[],
        filters=[{"table": "memi", "column": "age", "comparison": "ge", "value": 65}],
    )
    out = run(tiny, asked)
    assert out["n1"]["result"]["groups"][0]["value"] == pytest.approx(800 / 12)
    assert "interview_filter" in {m["rule"] for m in out["n1"]["messages"]}


def test_a_total_is_the_mean_times_the_weighted_count(tiny):
    out = run(
        tiny,
        estimate(id="n1"),
        estimate(id="n2", statistic="total"),
        estimate(id="n3", **COUNT),
        estimate(
            id="n4", statistic="total", measure={"table": "fmli", "column": "fincbtxm"}
        ),
    )
    for region in ("1", "2", "C"):
        total = by_group(out["n2"])[region]
        assert total == pytest.approx(
            by_group(out["n1"])[region] * by_group(out["n3"])[region]
        )
    assert by_group(out["n4"])["1"] == pytest.approx(
        (50000 * 200 + 50000 * 300 + 52000 * 300) / 12
    )


def test_a_member_level_mean_is_blocked_and_not_computed(tiny):
    out = run(tiny, estimate(measure={"table": "memi", "column": "age"}, group_by=[]))
    assert out["n1"]["verdict"] == "block" and out["n1"].get("result") is None
    assert "member_level" in {m["rule"] for m in out["n1"]["messages"]}


# ── combine ──────────────────────────────────────────────────────────────────


def _est(values: dict, reps: dict | None = None, unit="usd_annual") -> Estimate:
    groups = [
        GroupEstimate(group={"region": k}, value=v, interviews=1, units=1)
        for k, v in values.items()
    ]
    est = Estimate(groups=groups, unit=unit)
    if reps:
        est = est.with_replicates(
            {(("region", k),): v for k, v in reps.items()}, (4, 0.0)
        )
    return est


def test_ratio_and_difference_are_group_by_group():
    a = _est({"1": 10.0, "2": 30.0, "C": 5.0})
    b = _est({"1": 20.0, "2": 60.0})
    r = {g.group["region"]: g.value for g in ratio(a, b).groups}
    assert r == {"1": 0.5, "2": 0.5, "C": None}  # C is on one side only
    d = {g.group["region"]: g.value for g in difference(a, b).groups}
    assert d == {"1": -10.0, "2": -30.0, "C": None}
    assert difference(a, b).unit == "usd_annual"


def test_a_ratio_error_comes_from_the_inputs_replicates():
    a = _est({"1": 10.0}, {"1": [10.0, 12.0, 8.0, 10.0]})
    b = _est({"1": 20.0}, {"1": [20.0, 20.0, 20.0, 20.0]})
    g = ratio(a, b).groups[0]
    # replicate ratios .5 .6 .4 .5: Σ(θr − θ)² = .02, / R = 4
    assert g.standard_error == pytest.approx((0.02 / 4) ** 0.5)
    assert ratio(a, _est({"1": 20.0})).groups[0].standard_error is None


def test_a_conversion_rescales_the_second_input():
    a = _est({"1": 1200.0})
    b = _est({"1": 100.0}, unit="usd_quarterly")
    assert ratio(a, b, factor=4.0).groups[0].value == pytest.approx(3.0)


def test_the_sample_like_request_runs_end_to_end(tiny):
    ratio_node = {
        "id": "n3",
        "kind": "combine",
        "statistic": "ratio",
        "inputs": {"numerator": "n1", "denominator": "n2"},
    }
    out = run(
        tiny,
        estimate(id="n1"),
        estimate(id="n2", measure={"table": "fmli", "column": "fincbtxm"}),
        ratio_node,
    )
    assert out["n3"]["verdict"] == "warn" and out["n3"]["method"] == "ratio"
    r = by_group(out["n3"])
    assert r["1"] == pytest.approx(by_group(out["n1"])["1"] / by_group(out["n2"])["1"])
    assert out["n3"]["messages"][0]["from"] == ["n1", "n2"]
    se = {
        g["group"]["region"]: g["standard_error"] for g in out["n3"]["result"]["groups"]
    }
    assert se["1"] is not None and se["1"] > 0


def test_a_combine_of_a_blocked_input_is_blocked(tiny):
    diff = {
        "id": "n3",
        "kind": "combine",
        "statistic": "difference",
        "inputs": {"first": "n1", "second": "n2"},
    }
    out = run(
        tiny,
        estimate(id="n1"),
        estimate(id="n2", group_by=[{"table": "fmli", "column": "cutenure"}]),
        diff,
    )
    rules = {m["rule"] for m in out["n3"]["messages"]}
    assert out["n3"]["verdict"] == "block" and "grouping_differs" in rules


# ── findings of the first evaluation (eval/baseline) ─────────────────────────


def _difference(tiny, second: dict) -> dict:
    diff = {"id": "n3", "kind": "combine", "statistic": "difference",
            "inputs": {"first": "n1", "second": "n2"}}  # fmt: skip
    out = run(tiny, estimate(id="n1", group_by=[]), second, diff)
    return {m["rule"] for m in out["n3"]["messages"]}


def test_the_same_year_caution_is_for_the_same_statistic_twice(tiny):
    """Two groups of one year are a sound difference; the same request twice
    over one year set means two years were intended."""
    renters = [
        {"table": "fmli", "column": "cutenure", "comparison": "eq", "value": "4"}
    ]
    assert "same_year_set" not in _difference(
        tiny, estimate(id="n2", group_by=[], filters=renters)
    )
    assert "same_year_set" in _difference(tiny, estimate(id="n2", group_by=[]))


def test_a_conversion_of_time_spans_is_said(tiny):
    ratio_node = {"id": "n3", "kind": "combine", "statistic": "ratio",
                  "inputs": {"numerator": "n1", "denominator": "n2"}}  # fmt: skip
    quarter = {
        **AS_RELEASED,
        "time_basis": {"value": "quarter", "evidence": "quarterly"},
    }
    out = run(
        tiny,
        estimate(id="n1", group_by=[], stated=quarter),
        estimate(id="n2", group_by=[], measure={"table": "fmli", "column": "fincbtxm"}),
        ratio_node,
    )
    said = {m["rule"]: m["text"] for m in out["n3"]["messages"]}
    assert said["converted"].startswith(
        "the denominator was converted from annual to quarterly amounts"
    )
    plain = run(
        tiny,
        estimate(id="n1", group_by=[]),
        estimate(id="n2", group_by=[], measure={"table": "fmli", "column": "fincbtxm"}),
        ratio_node,
    )
    assert "converted" not in {m["rule"] for m in plain["n3"]["messages"]}
