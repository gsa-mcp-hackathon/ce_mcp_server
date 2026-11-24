"""Means (Plan 5, "Mean methods" and "Mean rules").

`x` is the measure, `w` the weight (`finlwt21`, or 1 when unweighted):

- calendar pair and monthly rows: Σ(x·w) / Σ(w·mo_scope/12), per unit per year;
- annual and interview values: Σ(x·w·mo_scope) / Σ(w·mo_scope).

A time basis of quarter or month divides the annual figure by 4 or 12; an
interview value keeps the column's unit.
"""

from __future__ import annotations

from ce_measures.join import Frame
from ce_measures.measures.base import summarize
from ce_measures.models import Estimate, Rule, T

PER_YEAR = ("{x} * {w}", "{w} * mo_scope / 12.0")
MONTHS_IN_SCOPE = ("{x} * {w} * mo_scope", "{w} * mo_scope")


def do_calendar_pair_mean(frame: Frame, weight: str | None, **kw) -> Estimate:
    return summarize(frame, *PER_YEAR, weight, **kw)


def do_monthly_rows_mean(frame: Frame, weight: str | None, **kw) -> Estimate:
    return summarize(frame, *PER_YEAR, weight, **kw)


def do_annual_value_mean(frame: Frame, weight: str | None, **kw) -> Estimate:
    return summarize(frame, *MONTHS_IN_SCOPE, weight, **kw)


def do_interview_value_mean(frame: Frame, weight: str | None, **kw) -> Estimate:
    kw["scale"] = 1.0  # nothing is annualized: the result keeps the column's unit
    return summarize(frame, *MONTHS_IN_SCOPE, weight, **kw)


METHODS = {
    "calendar_pair": "do_calendar_pair_mean",
    "monthly_rows": "do_monthly_rows_mean",
    "annual_value": "do_annual_value_mean",
    "interview_value": "do_interview_value_mean",
}

MEAN_RULES: list[Rule] = [
    Rule(
        id="nominal_mean",
        level="block",
        when=[T("columns.measure.ce:scale", "eq", "nominal")],
        message="{column} is nominal: only counts and shares are valid.",
        property="ce:scale",
    ),
    Rule(
        id="ordinal_mean",
        level="block",
        when=[T("columns.measure.ce:scale", "eq", "ordinal")],
        message="{column} is ordinal: a mean needs an interval or ratio scale, and "
        "the median is not available yet.",
        property="ce:scale",
    ),
    Rule(
        id="suppressed_full",
        level="block",
        when=[T("columns.measure.ce:suppressed", "eq", "full")],
        message="{column} is fully suppressed: there is nothing to compute.",
        property="ce:suppressed",
    ),
    Rule(
        id="unweighted_with_standard_error",
        level="substitute",
        when=[
            T("resolved.weight.value", "missing"),
            T("resolved.standard_error.value", "true"),
            T("resolved.standard_error.source", "eq", "stated"),
        ],
        message="A standard error was asked for: the weighted method runs, since a "
        "simple-random-sample error is not valid for this design.",
        target="weighted",
    ),
    Rule(
        id="unweighted_mean",
        level="warn",
        when=[T("resolved.weight.value", "missing")],
        message="not weighted, so it describes the interviewed sample, not US "
        "consumer units.",
    ),
    Rule(
        id="topcoded_subgroup",
        level="warn",
        when=[
            T("columns.measure.ce:topcoded", "true"),
            T("derived.has_grouping", "true"),
        ],
        message="{column} is top-coded; subgroup means are not preserved under "
        "top-coding.",
        property="ce:topcoded",
    ),
    Rule(
        id="capped_topcode",
        level="warn",
        when=[T("columns.measure.ce:topcodeMethod", "eq", "capped_at_critical_value")],
        message="{column} is capped at the top-code, so the mean is biased downward.",
        property="ce:topcodeMethod",
    ),
    Rule(
        id="annual_value_reading",
        level="warn",
        when=[
            T("resolved.measure_kind.value", "in", ["annual_value", "interview_value"])
        ],
        message="the months-in-scope average is this project's reading of the BLS "
        "guide.",
    ),
    Rule(
        id="one_imputed_version",
        level="warn",
        when=[T("derived.one_imputed_version", "true")],
        message="one imputed version understates the uncertainty.",
    ),
]
