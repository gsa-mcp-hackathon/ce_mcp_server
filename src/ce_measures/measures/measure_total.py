"""Totals (Plan 5, "Total"): population aggregates.

- calendar pair and monthly rows: Σ(x·w);
- annual and interview values: Σ(x·w·mo_scope) / 12.

Unweighted, it is the same sum with every weight 1: a sum over the interviewed
sample, not a population total (P4).
"""

from __future__ import annotations

from ce_measures.join import Frame
from ce_measures.measures.base import summarize
from ce_measures.models import Estimate, Rule, T

SUM = "{x} * {w}"
MONTHS_IN_SCOPE = "{x} * {w} * mo_scope / 12.0"


def do_calendar_pair_total(frame: Frame, weight: str | None, **kw) -> Estimate:
    return summarize(frame, SUM, None, weight, **kw)


def do_monthly_rows_total(frame: Frame, weight: str | None, **kw) -> Estimate:
    return summarize(frame, SUM, None, weight, **kw)


def do_annual_value_total(frame: Frame, weight: str | None, **kw) -> Estimate:
    return summarize(frame, MONTHS_IN_SCOPE, None, weight, **kw)


METHODS = {
    "calendar_pair": "do_calendar_pair_total",
    "monthly_rows": "do_monthly_rows_total",
    "annual_value": "do_annual_value_total",
    # the total of family size is the number of people in consumer units
    "interview_value": "do_annual_value_total",
}

TOTAL_RULES: list[Rule] = [
    Rule(
        id="not_additive",
        level="block",
        when=[T("columns.measure.ce:additiveOver", "lacks", "consumer_units")],
        message="{column} does not add up over consumer units, so a total is "
        "meaningless.",
        property="ce:additiveOver",
    ),
    Rule(
        id="suppressed_full",
        level="block",
        when=[T("columns.measure.ce:suppressed", "eq", "full")],
        message="{column} is fully suppressed: there is nothing to compute.",
        property="ce:suppressed",
    ),
    Rule(
        id="unweighted_total",
        level="warn",
        when=[T("resolved.weight.value", "missing")],
        message="not weighted, so it is a sum over the interviewed sample, not a "
        "population total.",
    ),
    Rule(
        id="capped_topcode",
        level="warn",
        when=[T("columns.measure.ce:topcodeMethod", "eq", "capped_at_critical_value")],
        message="{column} is capped at the top-code, so the total is biased downward.",
        property="ce:topcodeMethod",
    ),
]
