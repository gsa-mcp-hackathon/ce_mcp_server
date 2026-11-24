"""Rule validation: facts, paths, every rule row, precedence, flags.

Each rule of the plan's tables has a case that fires it; the clean node fires
none of the shared rules. Verdicts read no data: these tests run on the real
sidecar set with no data folder.
"""

import pytest

from ce_measures.combine import COMBINE_RULES
from ce_measures.errors import UnknownPath
from ce_measures.facts import collect
from ce_measures.measures.measure_count import COUNT_RULES
from ce_measures.measures.measure_mean import MEAN_RULES
from ce_measures.measures.measure_total import TOTAL_RULES
from ce_measures.models import Rule, T
from ce_measures.records import nodes
from ce_measures.verdict import (
    FLAG_OUTCOMES,
    SE_RULES,
    SHARED_RULES,
    evaluate,
    judge,
    lookup,
)
from ce_meta.sidecars import CheckResult, Failure, Report
from tests.measures.conftest import estimate, node_of, pair_of

CLEAN = estimate(
    measure={"table": "fmli", "column": "fincbtxm"},
    group_by=[],
    period={"text": "in 2024", "kind": "year", "values": [2024]},
    stated={},
)


def _judge(
    ctx,
    asked=None,
    *,
    flags=(),
    report=None,
    loaded=None,
    errors=None,
    derived=None,
    columns=None,
    drop=(),
):
    node = nodes(pair_of(ctx, asked or estimate(), flags=flags))[0]
    if drop:  # resolved entries taken out, as an older derivation might leave them
        resolved = {k: v for k, v in node.resolved.items() if k not in drop}
        node = node.model_copy(update={"resolved": resolved})
    facts = collect(
        node, ctx.sidecars, report or ctx.report, list(flags), loaded, errors
    )
    if derived:
        facts = facts.model_copy(update={"derived": facts.derived | derived})
    if columns:
        cols = {k: dict(v) for k, v in facts.columns.items()}
        for key, more in columns.items():
            cols[key] |= more
        facts = facts.model_copy(update={"columns": cols})
    return judge(node, facts)


def fired(ctx, asked=None, **kw) -> set[str]:
    return {m.rule for m in _judge(ctx, asked, **kw).messages}


# ── facts and paths ──────────────────────────────────────────────────────────


def test_derived_facts(real_ctx):
    node = node_of(
        real_ctx,
        estimate(
            measure={"table": "mtbi", "column": "cost"},
            group_by=[{"table": "fmli", "column": "state"}],
            period={"text": "2024 and 2025", "kind": "years", "values": [2024, 2025]},
        ),
    )
    d = collect(node, real_ctx.sidecars, real_ctx.report, []).derived
    assert d["geography_rating"] == "limited"
    assert d["spans_break"]
    assert d["breaks"] == ["BRK_2025_SAMPLE_REDESIGN", "BRK_2025_COLLECTION_GAP"]
    assert d["period_from_2025"] and d["has_grouping"] and not d["has_filter"]
    assert d["item_code_unfiltered"] and not d["measure_on_expn"]
    assert d["tables_without_route"] == [] and d["table_failures"] == []
    expn = node_of(real_ctx, measure={"table": "expn_rnt", "column": "qrt3mcmx"})
    assert collect(expn, real_ctx.sidecars, real_ctx.report, []).derived[
        "measure_on_expn"
    ]


def test_a_column_missing_from_its_sidecar_has_no_facts_and_is_blocked(real_ctx):
    node = node_of(real_ctx, measure={"table": "fmli", "column": "nosuch"})
    facts = collect(node, real_ctx.sidecars, real_ctx.report, [])
    assert facts.columns["measure"]["found"] is False
    assert "measure_role" in {m.rule for m in judge(node, facts).messages}


def test_lookup_paths(real_ctx):
    node = node_of(
        real_ctx,
        group_by=[
            {"table": "fmli", "column": "region"},
            {"table": "fmli", "column": "state"},
        ],
    )
    facts = collect(node, real_ctx.sidecars, real_ctx.report, ["needs_review: x"])
    assert lookup("resolved.weight.value", node, facts) == "finlwt21"
    assert lookup("columns.measure.ce:unit", node, facts) == "usd_quarterly"
    assert lookup("columns.group_by.1.ce:recoded", node, facts) is True
    assert lookup("columns.group_by.*.ce:geographyLevel", node, facts) == [
        "region",
        "state",
    ]
    assert lookup("resolved.nothing.here", node, facts) is None
    assert lookup("flags", node, facts) == ["needs_review"]
    with pytest.raises(UnknownPath):
        lookup("elsewhere.x", node, facts)
    with pytest.raises(UnknownPath):
        lookup("column.ce:role", node, facts)  # only in a rule with `each`


def test_every_rule_path_resolves(real_ctx):
    """A mistake in a rule table is caught here, not when a request hits it."""
    node = node_of(real_ctx)
    facts = collect(node, real_ctx.sidecars, real_ctx.report, [])
    column = facts.columns["measure"]
    for rule in [
        *SHARED_RULES,
        *SE_RULES,
        *MEAN_RULES,
        *COUNT_RULES,
        *TOTAL_RULES,
        *COMBINE_RULES,
    ]:
        for test in rule.when:
            lookup(test.on, node, facts, column if rule.each else None)


# ── the shared rules, row by row ─────────────────────────────────────────────

SHARED_BLOCKS = {
    "set_failure": dict(flags=["sidecar_set_invalid: the master is unreadable"]),
    "table_failure": dict(
        report=Report(
            checks=(
                CheckResult(
                    "c",
                    "d",
                    "failed",
                    (Failure("table", "ce-interview-fmli", "bad key"),),
                ),
            )
        )
    ),
    "period_not_loaded": dict(flags=["period_not_loaded: n1 2024"]),
    "capture_check_error": dict(errors=[{"message": "measure.column is unknown"}]),
    "measure_role": dict(
        asked=estimate(measure={"table": "fmli", "column": "cutenure"})
    ),
    "grouping_role": dict(
        asked=estimate(group_by=[{"table": "fmli", "column": "totexppq"}])
    ),
    "count_names_measure": dict(asked=estimate(statistic="count")),
    "unverified_core": dict(
        asked=estimate(measure={"table": "itbi", "column": "value"})
    ),
    "measure_kind_unresolved": dict(
        asked=estimate(measure={"table": "itbi", "column": "value"})
    ),
    "geography_unsupported": dict(
        asked=estimate(group_by=[{"table": "fmli", "column": "psu"}])
    ),
    "adjusted_calendar_pair": dict(
        asked=estimate(stated={"gap_2025": {"value": "adjusted", "evidence": "x"}})
    ),
    "no_weight_route": dict(
        asked=estimate(measure={"table": "mtbi", "column": "cost"}),
        drop=["weight_route"],
    ),
    "member_level": dict(
        asked=estimate(measure={"table": "memi", "column": "age"}),
        columns={"measure": {"ce:role": "measure", "ce:level": "member"}},
        derived={"measure_level": "member"},
    ),
    "expn_measure": dict(
        asked=estimate(measure={"table": "expn_rnt", "column": "qrt3mcmx"})
    ),
    "grouping_other_table": dict(
        asked=estimate(
            statistic="count",
            measure=None,
            group_by=[{"table": "memi", "column": "sex"}],
        )
    ),
    "part_year_not_built": dict(
        asked=estimate(
            period={"text": "Q1 2025", "kind": "quarter", "values": ["2025Q1"]}
        )
    ),
    "share_not_built": dict(asked=estimate(statistic="share", measure=None)),
}

SHARED_WARNINGS = {
    "unverified_fact": dict(asked=estimate()),
    "geography_limited": dict(
        asked=estimate(group_by=[{"table": "fmli", "column": "state"}])
    ),
    "recoded_grouping": dict(
        asked=estimate(group_by=[{"table": "fmli", "column": "state"}])
    ),
    "suppressed_partial": dict(
        asked=estimate(group_by=[{"table": "fmli", "column": "state"}])
    ),
    "region_code_c": dict(asked=estimate()),
    "gap_2025_as_released": dict(asked=estimate()),
    "break_spanned": dict(
        asked=estimate(
            period={"text": "2024 to 2025", "kind": "years", "values": [2024, 2025]}
        )
    ),
    "interview_filter": dict(
        asked=estimate(
            filters=[
                {"table": "memi", "column": "age", "comparison": "ge", "value": 65}
            ]
        )
    ),
    "item_code_unfiltered": dict(
        asked=estimate(measure={"table": "mtbi", "column": "cost"})
    ),
    "adjusted_monthly_rows": dict(
        asked=estimate(
            measure={"table": "mtbi", "column": "cost"},
            stated={"gap_2025": {"value": "adjusted", "evidence": "x"}},
        )
    ),
}


# Each line of the plan's table "Rules shared by every measure", with its rules.
PLAN_ROWS = {
    "set-level failure": ["set_failure"],
    "table-level failure": ["table_failure"],
    "period not loaded": ["period_not_loaded"],
    "capture check error": ["capture_check_error"],
    "a column cannot fill its slot": [
        "measure_role",
        "grouping_role",
        "count_names_measure",
    ],
    "VERIFY on role, scale or unit; kind unresolved": [
        "unverified_core",
        "measure_kind_unresolved",
    ],
    "geography unsupported": ["geography_unsupported"],
    "adjusted calendar pair": ["adjusted_calendar_pair"],
    "no weight route": ["no_weight_route"],
    "member-level mean or total": ["member_level"],
    "detailed expenditure measure": ["expn_measure"],
    "other VERIFY or needs-review": ["unverified_fact"],
    "geography limited": ["geography_limited"],
    "recoded grouping": ["recoded_grouping"],
    "partly suppressed grouping": ["suppressed_partial"],
    "region from 2025": ["region_code_c"],
    "2025 as released": ["gap_2025_as_released"],
    "across a break": ["break_spanned"],
    "interview filters": ["interview_filter"],
    "item codes not filtered": ["item_code_unfiltered"],
    "adjusted monthly rows": ["adjusted_monthly_rows"],
    # parts this build has no method for yet
    "added: grouping on a third table": ["grouping_other_table"],
    "added: part of a year": ["part_year_not_built"],
    "added: share": ["share_not_built"],
}


def test_every_shared_rule_has_a_case_and_a_plan_row():
    ids = [r.id for r in SHARED_RULES]
    assert len(ids) == len(set(ids))
    assert set(ids) == set(SHARED_BLOCKS) | set(SHARED_WARNINGS)
    assert sorted(ids) == sorted(i for row in PLAN_ROWS.values() for i in row)
    assert len(PLAN_ROWS) == 21 + 3  # the plan's 21 lines, and the three added


@pytest.mark.parametrize("rule", sorted(SHARED_BLOCKS))
def test_a_shared_block(real_ctx, rule):
    verdict = _judge(real_ctx, **SHARED_BLOCKS[rule])
    assert rule in {m.rule for m in verdict.messages}
    assert verdict.level == "block"


@pytest.mark.parametrize("rule", sorted(SHARED_WARNINGS))
def test_a_shared_warning(real_ctx, rule):
    verdict = _judge(real_ctx, **SHARED_WARNINGS[rule])
    found = {m.rule: m for m in verdict.messages}
    assert rule in found and found[rule].level == "warn"


def test_the_clean_node_fires_no_shared_rule(real_ctx):
    shared = {r.id for r in SHARED_RULES}
    assert fired(real_ctx, CLEAN) & shared == set()


def test_a_message_names_its_column_and_property(real_ctx):
    verdict = _judge(
        real_ctx, estimate(group_by=[{"table": "fmli", "column": "state"}])
    )
    by_rule = {m.rule: m for m in verdict.messages}
    assert by_rule["recoded_grouping"].column == "state"
    assert by_rule["recoded_grouping"].property == "ce:recoded"
    assert by_rule["unverified_fact"].property == "needs-review"
    assert (
        by_rule["unverified_fact"].text
        == "totexpcq is still under review in the sidecar."
    )


def test_region_code_c_replaces_partial_suppression_from_2025(real_ctx):
    assert "suppressed_partial" not in fired(real_ctx)
    before = estimate(period={"text": "2024", "kind": "year", "values": [2024]})
    rules = fired(real_ctx, before)
    assert "suppressed_partial" in rules and "region_code_c" not in rules


def test_the_gap_warning_is_for_weighted_estimates(real_ctx):
    unweighted = estimate(
        stated={
            "gap_2025": {"value": "as_released", "evidence": "as released"},
            "weighted": {"value": False, "evidence": "unweighted"},
        }
    )
    assert "gap_2025_as_released" not in fired(real_ctx, unweighted)


def test_the_period_not_loaded_on_a_run(real_ctx):
    verdict = _judge(real_ctx, loaded=set())
    assert verdict.level == "block" and "period_not_loaded" in {
        m.rule for m in verdict.messages
    }
    loaded = {(2025, "1x"), (2025, "2"), (2025, "3"), (2025, "4"), (2026, "1")}
    assert "period_not_loaded" not in fired(real_ctx, loaded=loaded)


# ── the measure tables ───────────────────────────────────────────────────────

FINCBTXM = {"table": "fmli", "column": "fincbtxm"}
MEASURE_CASES = {
    "nominal_mean": dict(
        asked=estimate(measure={"table": "fmli", "column": "cutenure"})
    ),
    "ordinal_mean": dict(
        asked=estimate(), columns={"measure": {"ce:scale": "ordinal"}}
    ),
    "suppressed_full": dict(
        asked=estimate(), columns={"measure": {"ce:suppressed": "full"}}
    ),
    "unweighted_with_standard_error": dict(
        asked=estimate(
            stated={
                "gap_2025": {"value": "as_released", "evidence": "as released"},
                "weighted": {"value": False, "evidence": "unweighted"},
                "standard_error": {"value": True, "evidence": "with its error"},
            }
        )
    ),
    "unweighted_mean": dict(
        asked=estimate(stated={"weighted": {"value": False, "evidence": "unweighted"}})
    ),
    "topcoded_subgroup": dict(asked=estimate(measure=FINCBTXM)),
    "capped_topcode": dict(
        asked=estimate(),
        columns={"measure": {"ce:topcodeMethod": "capped_at_critical_value"}},
    ),
    "annual_value_reading": dict(asked=estimate(measure=FINCBTXM)),
    "one_imputed_version": dict(
        asked=estimate(
            measure={"table": "fmli", "column": "fincbtx1"},
            stated={"imputation": {"value": "version_1", "evidence": "version one"}},
        )
    ),
}
COUNT = dict(statistic="count", measure=None)
COUNT_CASES = {
    "member_weighted_count": dict(
        asked=estimate(
            **COUNT, stated={"count_unit": {"value": "member", "evidence": "x"}}
        )
    ),
    "member_count": dict(
        asked=estimate(
            **COUNT,
            stated={
                "count_unit": {"value": "member", "evidence": "people"},
                "weighted": {"value": False, "evidence": "unweighted"},
            },
        )
    ),
    "unweighted_count": dict(
        asked=estimate(**COUNT, stated={"weighted": {"value": False, "evidence": "x"}})
    ),
    "distinct_with_filter": dict(
        asked=estimate(
            **COUNT,
            filters=[
                {
                    "table": "fmli",
                    "column": "cutenure",
                    "comparison": "eq",
                    "value": "4",
                }
            ],
            stated={"weighted": {"value": False, "evidence": "x"}},
        )
    ),
}
TOTAL = dict(statistic="total")
TOTAL_CASES = {
    "not_additive": dict(
        asked=estimate(**TOTAL, measure={"table": "fmli", "column": "mo_scope"})
    ),
    "suppressed_full": dict(
        asked=estimate(**TOTAL), columns={"measure": {"ce:suppressed": "full"}}
    ),
    "unweighted_total": dict(
        asked=estimate(**TOTAL, stated={"weighted": {"value": False, "evidence": "x"}})
    ),
    "capped_topcode": dict(
        asked=estimate(**TOTAL),
        columns={"measure": {"ce:topcodeMethod": "capped_at_critical_value"}},
    ),
}
SE_CASES = {"topcoded_variance": dict(asked=estimate(measure=FINCBTXM))}


@pytest.mark.parametrize(
    "table,cases",
    [
        (MEAN_RULES, MEASURE_CASES),
        (COUNT_RULES, COUNT_CASES),
        (TOTAL_RULES, TOTAL_CASES),
        (SE_RULES, SE_CASES),
    ],
    ids=["mean", "count", "total", "standard_error"],
)
def test_every_measure_rule_fires_on_its_case(real_ctx, table, cases):
    assert {r.id for r in table} == set(cases)
    for rule in table:
        assert rule.id in fired(real_ctx, **cases[rule.id]), rule.id


def test_the_measure_rules_do_not_fire_on_the_clean_count(real_ctx):
    count = estimate(**COUNT, group_by=[], stated={})
    assert fired(real_ctx, count) & {r.id for r in COUNT_RULES} == set()


# ── precedence, substitution, notes ──────────────────────────────────────────


def test_block_wins_and_warnings_are_kept(real_ctx):
    verdict = _judge(real_ctx, **SHARED_BLOCKS["adjusted_calendar_pair"])
    levels = {m.level for m in verdict.messages}
    assert verdict.level == "block" and {"block", "warn"} <= levels


def test_a_substitution_runs_the_weighted_method_after_one_recheck(real_ctx):
    verdict = _judge(real_ctx, **MEASURE_CASES["unweighted_with_standard_error"])
    assert verdict.level == "substitute" and verdict.weighted
    assert verdict.substituted_from == "do_calendar_pair_mean, unweighted"
    rules = [m.rule for m in verdict.messages]
    assert rules[0] == "unweighted_with_standard_error"
    # the re-check sees a weighted node: no unweighted warning, the 2025 one kept
    assert "unweighted_mean" not in rules and "gap_2025_as_released" in rules
    assert "weight finlwt21: substituted" in verdict.notes


def test_a_block_found_on_the_recheck_still_blocks(real_ctx):
    case = MEASURE_CASES["unweighted_with_standard_error"]
    verdict = _judge(real_ctx, **case, columns={"measure": {"ce:scale": "nominal"}})
    assert verdict.level == "block"


def test_substitution_does_not_chain(real_ctx):
    from ce_measures.models import Node, NodeFacts

    always = Rule(
        id="again",
        level="substitute",
        when=[T("derived.statistic", "present")],
        message="m",
        target="weighted",
    )
    node = Node(id="n", kind="estimate", asked={"statistic": "mean"}, resolved={})
    facts = NodeFacts(
        columns={}, master={}, flags=[], flag_text=[], derived={"statistic": "mean"}
    )
    verdict = evaluate(node, facts, [always], "do_x")
    assert verdict.level == "substitute"


def test_defaulted_choices_are_notes(real_ctx):
    verdict = _judge(real_ctx, estimate(measure=FINCBTXM))
    assert verdict.notes == [
        "weight finlwt21: defaulted",
        "time basis year: defaulted",
        "missing codes removed: defaulted",
        "five versions averaged: defaulted",
    ]


# ── flags ────────────────────────────────────────────────────────────────────


def test_every_flag_outcome_names_a_rule_that_exists():
    ids = {r.id for t in (SHARED_RULES, COUNT_RULES, MEAN_RULES) for r in t}
    for code, (kind, rule) in FLAG_OUTCOMES.items():
        assert kind in ("rule", "warn", "note"), code
        if kind == "rule":
            assert rule in ids, code
    assert len(FLAG_OUTCOMES) == 9  # one per line of the plan's flag table


def test_flags_become_warnings_and_notes(real_ctx):
    flags = [
        "statistic_term_differs: n1 is mean; the text says 'total'",
        "imputed_version_averaged: n1 named fincbtx1; resolved to fincbtxm",
        "something_new: n1 a flag no table knows",
        "statistic_term_differs: n2 is total",  # another node's: not this one
    ]
    verdict = _judge(real_ctx, CLEAN, flags=flags)
    warned = {m.rule: m.text for m in verdict.messages if m.level == "warn"}
    assert warned["statistic_term_differs"].startswith("n1 is mean")
    assert warned["flag"] == "n1 a flag no table knows"
    assert "n1 named fincbtx1; resolved to fincbtxm" in verdict.notes
    assert sum(m.rule == "statistic_term_differs" for m in verdict.messages) == 1


def test_a_flag_on_a_column_concerns_the_nodes_that_use_it(real_ctx):
    flags = ["needs_review: fmli.totexpcq"]
    node = node_of(real_ctx, CLEAN)
    facts = collect(node, real_ctx.sidecars, real_ctx.report, flags)
    assert facts.flags == []
    node = node_of(real_ctx)
    assert collect(node, real_ctx.sidecars, real_ctx.report, flags).flags == [
        "needs_review"
    ]


# ── findings of the first evaluation (eval/baseline) ─────────────────────────


def test_a_filter_on_a_state_is_cautioned_as_a_grouping_by_state_is(real_ctx):
    texas = {"table": "fmli", "column": "state", "comparison": "eq", "value": "48"}
    assert "geography_limited" in fired(
        real_ctx, estimate(group_by=[], filters=[texas])
    )
    psu = {"table": "fmli", "column": "psu", "comparison": "eq", "value": "S11A"}
    verdict = _judge(real_ctx, estimate(group_by=[], filters=[psu]))
    assert verdict.level == "block"
    assert "geography_unsupported" in {m.rule for m in verdict.messages}
    west = {"table": "fmli", "column": "region", "comparison": "eq", "value": "4"}
    assert "geography_limited" not in fired(
        real_ctx, estimate(group_by=[], filters=[west])
    )


def test_a_quantity_used_to_group_can_also_be_averaged(real_ctx):
    """Household size is a dimension with a ratio scale and a unit: a mean of
    it is an interview value. A coded dimension is still refused."""
    size = _judge(real_ctx, estimate(measure={"table": "fmli", "column": "fam_size"}))
    assert size.level == "warn" and size.method == "do_interview_value_mean"
    assert "measure_role" not in {m.rule for m in size.messages}
    assert "measure_role" in fired(
        real_ctx, estimate(measure={"table": "fmli", "column": "cutenure"})
    )
