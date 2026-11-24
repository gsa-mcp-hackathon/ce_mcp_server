"""Counts (Plan 5, "Count"). Four counts, each with one name everywhere:

| Name | Method |
| --- | --- |
| interviews | `do_row_count`: rows of the frame |
| consumer units interviewed | `do_distinct_count`: distinct cuid |
| consumer units in the population | `do_weighted_count`: Σ(w·mo_scope/12) |
| members interviewed | `do_distinct_member_count`: distinct (cuid, membno) |
"""

from __future__ import annotations

from ce_measures.join import Frame
from ce_measures.measures.base import count, summarize
from ce_measures.models import Estimate, Rule, T

NAMES = {
    "do_row_count": "interviews",
    "do_distinct_count": "consumer units interviewed",
    "do_weighted_count": "consumer units in the population",
    "do_distinct_member_count": "members interviewed",
}


def do_weighted_count(frame: Frame, weight: str | None, **kw) -> Estimate:
    return summarize(frame, "{w} * mo_scope / 12.0", None, weight, **kw)


def do_distinct_count(frame: Frame, weight: str | None = None, **kw) -> Estimate:
    return count(frame, "units", kw.get("unit") or "count")


def do_row_count(frame: Frame, weight: str | None = None, **kw) -> Estimate:
    return count(frame, "rows", kw.get("unit") or "count")


def do_distinct_member_count(frame: Frame, weight: str | None = None, **kw) -> Estimate:
    return count(frame, "members", kw.get("unit") or "count")


METHODS = {
    "weighted_population": "do_weighted_count",
    "distinct_cuid": "do_distinct_count",
    "row_count": "do_row_count",
    "distinct_member": "do_distinct_member_count",
}

COUNT_RULES: list[Rule] = [
    Rule(
        id="member_weighted_count",
        level="block",
        when=[
            T("resolved.count_unit.value", "eq", "member"),
            T("derived.weighted", "true"),
        ],
        message="The consumer-unit weight has no direct use on member rows; an "
        "unweighted count of members interviewed is available.",
    ),
    Rule(
        id="member_count",
        level="warn",
        when=[T("resolved.count_unit.value", "eq", "member")],
        message="a member is counted once if any of their interviews matches.",
    ),
    Rule(
        id="unweighted_count",
        level="warn",
        when=[T("derived.weighted", "false")],
        message="a count of the interviewed sample, not of {detail}.",
        detail="derived.population_word",
    ),
    Rule(
        id="distinct_with_filter",
        level="warn",
        when=[
            T("resolved.count_method.value", "eq", "distinct_cuid"),
            T("derived.has_filter", "true"),
        ],
        message="a consumer unit is counted when any of its interviews matches.",
    ),
]
