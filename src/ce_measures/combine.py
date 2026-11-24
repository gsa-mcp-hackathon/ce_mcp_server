"""Combine nodes: a ratio or a difference of other nodes' results (P11).

A combine node works on its inputs' results, group by group, never on rows:
a ratio of two means is the ratio of the estimates, not the mean of
per-interview ratios. Groups are matched by their codes; a group on one side
only has a null value. A ratio's or a difference's standard error comes from
the inputs' replicate estimates, kept until the combine nodes are done.
"""

from __future__ import annotations

import math

import numpy as np

from ce_measures import replicates as rep
from ce_measures.facts import breaks_between, years
from ce_measures.models import (
    Estimate,
    GroupEstimate,
    Message,
    Node,
    NodeFacts,
    NodeOutcome,
    Rule,
    T,
    Verdict,
)
from ce_measures.verdict import evaluate

ROLES = {"ratio": ("numerator", "denominator"), "difference": ("first", "second")}
MONTHS = {"usd_annual": 12, "usd_quarterly": 3, "usd_monthly": 1}

COMBINE_RULES: list[Rule] = [
    Rule(
        id="input_blocked",
        level="block",
        when=[T("derived.blocked_inputs", "present")],
        message="An input is blocked, so it cannot be combined. {detail}.",
        detail="derived.blocked_inputs",
    ),
    Rule(
        id="grouping_differs",
        level="block",
        when=[T("derived.grouping_differs", "present")],
        message="The inputs group differently: {detail}.",
        detail="derived.grouping_differs",
    ),
    Rule(
        id="year_sets_differ",
        level="block",
        when=[
            T("derived.statistic", "eq", "ratio"),
            T("derived.year_sets_differ", "present"),
        ],
        message="A ratio's inputs cover different years: {detail}.",
        detail="derived.year_sets_differ",
    ),
    Rule(
        id="units_differ",
        level="block",
        when=[T("derived.units_differ", "present")],
        message="The inputs' units differ ({detail}); convert before combining.",
        detail="derived.units_differ",
    ),
    Rule(
        id="carried",
        level="warn",
        when=[T("derived.warned_inputs", "present")],
        message="the cautions on {detail} apply.",
        detail="derived.carried_text",
    ),
    Rule(
        id="converted",
        level="warn",
        when=[T("derived.conversion", "present")],
        message="{detail} before combining, so both cover the same span of time; "
        "the amounts as stated would give another figure.",
        detail="derived.conversion",
    ),
    Rule(
        id="difference_break",
        level="warn",
        when=[
            T("derived.statistic", "eq", "difference"),
            T("derived.spans_break", "true"),
        ],
        message="The difference spans {detail}; it may reflect the method change.",
        detail="derived.breaks",
    ),
    Rule(
        id="same_year_set",
        level="warn",
        when=[
            T("derived.statistic", "eq", "difference"),
            T("derived.same_year_set", "true"),
        ],
        message="Both inputs use the same year set; each year needs its own "
        "first-quarter file.",
    ),
]


def inputs(node: Node) -> list[str]:
    given = node.asked.get("inputs") or {}
    return [given.get(r) for r in ROLES.get(str(node.asked.get("statistic")), ())]


def _reason(outcome: NodeOutcome | None) -> str:
    if outcome is None:
        return "no such node"
    first = next((m for m in outcome.verdict.messages if m.level == "block"), None)
    return first.text.rstrip(".") if first else "blocked"


SPAN_WORDS = {
    "usd_annual": "annual",
    "usd_quarterly": "quarterly",
    "usd_monthly": "monthly",
}


def _same_request(found: list[Node | None]) -> bool:
    """Whether two input nodes ask for the same statistic of the same rows."""
    if len(found) != 2 or None in found:
        return False
    keys = ("statistic", "measure", "filters", "group_by")
    a, b = ({k: n.asked.get(k) or None for k in keys} for n in found)
    return a == b


def _conversion_text(node: Node) -> str | None:
    """The resolved conversion in plain words, or None when there is none."""
    text = str((node.resolved.get("conversion") or {}).get("value") or "none")
    if text == "none" or " from " not in text:
        return None
    role, spans = text.split(" from ", 1)
    source, _, target = spans.partition(" to ")
    return (
        f"the {role} was converted from {SPAN_WORDS.get(source, source)} to "
        f"{SPAN_WORDS.get(target, target)} amounts"
    )


def _grouping(node: Node | None) -> list[tuple[str, str]]:
    if node is None:
        return []
    return sorted((r["table"], r["column"]) for r in node.asked.get("group_by") or [])


def _unit(node: Node, role: str) -> str | None:
    return ((node.resolved.get("inputs") or {}).get(role) or {}).get("unit")


def conversion_factor(node: Node) -> float:
    """What the second input is multiplied by: the resolved conversion of one
    time span into another, such as quarterly into annual dollars."""
    text = str((node.resolved.get("conversion") or {}).get("value") or "none")
    if text == "none" or " from " not in text:
        return 1.0
    _, spans = text.split(" from ", 1)
    source, _, target = spans.partition(" to ")
    if source in MONTHS and target in MONTHS:
        return MONTHS[target] / MONTHS[source]
    return 1.0


def combine_facts(
    node: Node, outcomes: dict[str, NodeOutcome], nodes: dict[str, Node], master: dict
) -> NodeFacts:
    statistic = str(node.asked.get("statistic"))
    ids = inputs(node)
    roles = ROLES.get(statistic, ())
    found = [nodes.get(i) for i in ids]
    blocked = [
        f"{i}: {_reason(outcomes.get(i))}"
        for i in ids
        if i not in outcomes or outcomes[i].verdict.level == "block"
    ]
    warned = [
        i
        for i in ids
        if i in outcomes and outcomes[i].verdict.level in ("warn", "substitute")
    ]
    g1, g2 = (_grouping(n) for n in (found + [None, None])[:2])
    y1, y2 = (sorted(years(n)) if n else [] for n in (found + [None, None])[:2])
    grouping = (
        f"{ids[0]} by {g1 or 'nothing'}, {ids[1]} by {g2 or 'nothing'}"
        if len(ids) == 2 and g1 != g2
        else None
    )
    year_text = f"{ids[0]} {y1}, {ids[1]} {y2}" if len(ids) == 2 and y1 != y2 else None
    u1, u2 = (_unit(node, r) for r in roles) if len(roles) == 2 else (None, None)
    converted = conversion_factor(node) != 1.0
    if statistic == "difference":
        units = None if (u1 == u2 or converted) and u1 is not None else f"{u1}, {u2}"
    else:
        units = None if u1 is not None and u2 is not None else f"{u1}, {u2}"
    derived = {
        "statistic": statistic,
        "inputs": ids,
        "blocked_inputs": blocked,
        "warned_inputs": warned,
        "carried_text": "both inputs"
        if len(warned) == 2
        else f"input {', '.join(warned)}",
        "grouping_differs": grouping,
        "year_sets_differ": year_text,
        "units_differ": units,
        "breaks": breaks_between(y1 + y2, master),
        "spans_break": bool(breaks_between(y1 + y2, master)),
        # the same statistic twice over one year set: two years were meant.
        # Two regions, or two measures, of one year are a sound difference.
        "same_year_set": bool(y1) and y1 == y2 and _same_request(found),
        "conversion": _conversion_text(node),
    }
    return NodeFacts(columns={}, master=master, flags=[], flag_text=[], derived=derived)


def judge(
    node: Node, outcomes: dict[str, NodeOutcome], nodes: dict[str, Node], master: dict
) -> Verdict:
    facts = combine_facts(node, outcomes, nodes, master)
    verdict = evaluate(node, facts, COMBINE_RULES, str(node.asked.get("statistic")))
    messages = [
        m.model_copy(update={"inputs": facts.derived["warned_inputs"]})
        if m.rule == "carried"
        else m
        for m in verdict.messages
    ]
    weighted = all(
        outcomes[i].verdict.weighted for i in facts.derived["inputs"] if i in outcomes
    )
    return verdict.model_copy(
        update={"messages": messages, "weighted": weighted, "notes": []}
    )


# ── the arithmetic ───────────────────────────────────────────────────────────


def _combine(
    a: Estimate, b: Estimate, op, unit: str | None, factor: float = 1.0
) -> Estimate:
    right = {g.key: g for g in b.groups}
    keys = [g.key for g in a.groups] + [
        k for k in right if k not in {g.key for g in a.groups}
    ]
    left = {g.key: g for g in a.groups}
    same_design = (
        a.variance_parameters is not None
        and a.variance_parameters == b.variance_parameters
    )
    groups, reps = [], {}
    for key in keys:
        ga, gb = left.get(key), right.get(key)
        value = None
        if (
            ga is not None
            and gb is not None
            and ga.value is not None
            and gb.value is not None
        ):
            value = op(ga.value, gb.value * factor)
        se = None
        ra, rb = a.replicates.get(key), b.replicates.get(key)
        if value is not None and same_design and ra is not None and rb is not None:
            with np.errstate(divide="ignore", invalid="ignore"):
                theta_r = op(np.asarray(ra), np.asarray(rb) * factor)
            if not np.isnan(theta_r).any():
                R, k = a.variance_parameters  # noqa: N806
                se = math.sqrt(
                    float(rep.variance(np.array([value]), np.array([theta_r]), R, k)[0])
                )
                reps[key] = list(theta_r)
        base = ga or gb
        groups.append(
            GroupEstimate(
                group=dict(key),
                value=value,
                standard_error=se,
                interviews=base.interviews,
                units=base.units,
            )
        )
    out = Estimate(
        groups=groups,
        unit=unit,
        standard_error_method="replicate weights of the inputs" if reps else None,
    )
    return out.with_replicates(reps, a.variance_parameters) if reps else out


def _ratio(x, y):
    if isinstance(x, np.ndarray):
        return np.where(y == 0, np.nan, x / np.where(y == 0, 1, y))
    return None if y == 0 else x / y


def ratio(
    numerator: Estimate,
    denominator: Estimate,
    unit: str | None = "proportion",
    factor: float = 1.0,
) -> Estimate:
    """The ratio of the two estimates, group by group."""
    return _combine(numerator, denominator, _ratio, unit, factor)


def difference(
    first: Estimate, second: Estimate, unit: str | None = None, factor: float = 1.0
) -> Estimate:
    """The first estimate minus the second, group by group."""
    return _combine(first, second, lambda x, y: x - y, unit or first.unit, factor)


__all__ = ["COMBINE_RULES", "Message", "difference", "judge", "ratio"]
