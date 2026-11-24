"""Rule validation (pipeline step 3): one verdict per node.

The rules are rows (P12). `SHARED_RULES` is the table "Rules shared by every
measure"; each measure module holds its own table; `combine.COMBINE_RULES`
judges combine nodes. `evaluate` is the only function that reads them.

Precedence is block, substitute, warn, pass. Warnings accumulate. After a
substitution the rules run once more on the substituted node, with no further
chaining. Every defaulted choice is reported as a note, never as a verdict.
"""

from __future__ import annotations

import string
from collections.abc import Sequence
from typing import Any

from ce_measures.errors import UnknownPath
from ce_measures.facts import flag_code, grouping_columns, used_columns
from ce_measures.models import (
    LEVEL_ORDER,
    Message,
    Node,
    NodeFacts,
    Rule,
    T,
    Test,
    Verdict,
)

ROOTS = ("asked", "resolved", "columns", "master", "derived", "flags", "column")
INDEXED = ("group_by", "filters")


# ── the shared rules ─────────────────────────────────────────────────────────

SHARED_RULES: list[Rule] = [
    # blocks
    Rule(
        id="set_failure",
        level="block",
        when=[T("derived.set_failures", "present")],
        message="No sidecar fact can be trusted: the sidecar set failed {detail}.",
        detail="derived.set_failures",
    ),
    Rule(
        id="table_failure",
        level="block",
        when=[T("derived.table_failures", "present")],
        message="The sidecar of a table this node uses failed a check: {detail}.",
        detail="derived.table_failures",
    ),
    Rule(
        id="period_not_loaded",
        level="block",
        when=[T("derived.period_not_loaded", "true")],
        message="No data is loaded for that period; the request can be replayed "
        "once the release is loaded.",
    ),
    Rule(
        id="capture_check_error",
        level="block",
        when=[T("derived.check_errors", "present")],
        message="The capture check failed: {detail}.",
        detail="derived.check_errors",
    ),
    Rule(
        id="measure_role",
        level="block",
        when=[
            T("asked.measure", "present"),
            T("derived.measure_usable", "false"),
        ],
        message="{column} is not a measure, so it cannot be averaged or summed.",
        property="ce:role",
    ),
    Rule(
        id="grouping_role",
        level="block",
        each="group_by",
        when=[T("column.ce:role", "not_in", ["dimension", "period"])],
        message="{column} is not a dimension or a period, so it cannot group a result.",
        property="ce:role",
    ),
    Rule(
        id="count_names_measure",
        level="block",
        when=[T("derived.statistic", "eq", "count"), T("asked.measure", "present")],
        message="A count takes no measure column.",
    ),
    Rule(
        id="unverified_core",
        level="block",
        each="used",
        when=[T("column.verify_core", "present")],
        message="{column}: {property} is still VERIFY in the sidecar, so the method "
        "cannot be chosen until it is reviewed.",
        property="column.verify_core",
    ),
    Rule(
        id="measure_kind_unresolved",
        level="block",
        when=[T("resolved.measure_kind.value", "eq", "unresolved")],
        message="The method cannot be chosen: {detail}.",
        detail="derived.unresolved_why",
    ),
    Rule(
        id="geography_unsupported",
        level="block",
        when=[T("derived.geography_rating", "eq", "unsupported")],
        message="The survey design does not support estimates at that geography level.",
    ),
    Rule(
        id="adjusted_calendar_pair",
        level="block",
        when=[
            T("resolved.gap_2025.value", "eq", "adjusted"),
            T("resolved.measure_kind.value", "eq", "calendar_pair"),
        ],
        message="The 2025 adjustment cannot be applied to the fmli summary columns; "
        "ask for the spending from mtbi.",
    ),
    Rule(
        id="no_weight_route",
        level="block",
        when=[T("derived.tables_without_route", "present")],
        message="{detail} has no route to the weight.",
        detail="derived.tables_without_route",
    ),
    Rule(
        id="member_level",
        level="block",
        when=[
            T("derived.statistic", "in", ["mean", "total"]),
            T("derived.measure_level", "eq", "member"),
        ],
        message="No method is defined for a member-level estimate; the table can "
        "still be used as a filter.",
        property="ce:level",
    ),
    Rule(
        id="expn_measure",
        level="block",
        when=[T("derived.measure_on_expn", "true")],
        message="No estimation method is defined for the detailed expenditure "
        "tables yet; they can still be used as filters.",
    ),
    # blocks this build adds: parts with no method written yet
    Rule(
        id="grouping_other_table",
        level="block",
        when=[T("derived.grouping_tables", "present")],
        message="Grouping by a column of {detail} is not supported yet: only fmli "
        "and the measure's own table can group a result.",
        detail="derived.grouping_tables",
    ),
    Rule(
        id="part_year_not_built",
        level="block",
        when=[T("derived.within_year", "true")],
        message="Estimates for part of a survey year are not built yet.",
    ),
    Rule(
        id="share_not_built",
        level="block",
        when=[T("derived.share", "true")],
        message="Shares are not built yet; a count by category is available.",
    ),
    # warnings
    Rule(
        id="unverified_fact",
        level="warn",
        each="used",
        when=[T("column.under_review", "true")],
        message="{column} is still under review in the sidecar.",
        property="column.review_property",
    ),
    Rule(
        id="geography_limited",
        level="warn",
        when=[T("derived.geography_rating", "eq", "limited")],
        message="No state weights exist after 2023, so a state figure rests on the "
        "national weight; region or division is reliable.",
    ),
    Rule(
        id="recoded_grouping",
        level="warn",
        each="group_by",
        when=[T("column.ce:recoded", "true")],
        message="{column} is recoded for disclosure, so group membership is "
        "approximate.",
        property="ce:recoded",
    ),
    Rule(
        id="suppressed_partial",
        level="warn",
        each="group_by",
        when=[T("column.ce:suppressed", "eq", "partial"), T("column.code_c", "false")],
        message="{column} is partly suppressed, so its groups are incomplete.",
        property="ce:suppressed",
    ),
    Rule(
        id="region_code_c",
        level="warn",
        each="group_by",
        when=[T("column.code_c", "true")],
        message='region code C ("Combined") is not a region; it is reported '
        "separately.",
    ),
    Rule(
        id="gap_2025_as_released",
        level="warn",
        when=[T("derived.as_released_2025", "true"), T("derived.weighted", "true")],
        message="2025 as released. Fourth-quarter interviews stand for one third of "
        "the usual population.",
    ),
    Rule(
        id="break_spanned",
        level="warn",
        when=[T("derived.spans_break", "true")],
        message="The period spans {detail}; a difference may reflect the method "
        "change.",
        detail="derived.breaks",
    ),
    Rule(
        id="interview_filter",
        level="warn",
        when=[T("derived.interview_filter_tables", "present")],
        message="An interview is kept when at least one row of {detail} matches.",
        detail="derived.interview_filter_tables",
    ),
    Rule(
        id="item_code_unfiltered",
        level="warn",
        when=[T("derived.item_code_unfiltered", "true")],
        message="The rows of {table} mix different items. In mtbi they include asset "
        "values and loan amounts, so the sum is not total spending.",
    ),
    Rule(
        id="adjusted_monthly_rows",
        level="warn",
        when=[
            T("resolved.gap_2025.value", "eq", "adjusted"),
            T("resolved.measure_kind.value", "eq", "monthly_rows"),
        ],
        message="BLS notes that differences from its published tables caused by "
        "top-coding and suppression are magnified by the adjustment.",
    ),
]

# Standard errors: a warning when the variance cannot be trusted. The
# multiple-imputation form is how the error is computed (a note), not a change
# of the estimate, so it is not a verdict.
SE_RULES: list[Rule] = [
    Rule(
        id="topcoded_variance",
        level="warn",
        when=[
            T("resolved.standard_error.value", "true"),
            T("columns.measure.ce:topcoded", "true"),
        ],
        message="{column} is top-coded; its variance is not preserved, so the "
        "standard error is approximate.",
        property="ce:topcoded",
    ),
]

# ── flags from capture ───────────────────────────────────────────────────────

# Every flag code Plan 4 raises, with its outcome: covered by a rule (that rule
# reads the same fact), a warning of its own, or a note.
FLAG_OUTCOMES: dict[str, tuple[str, str | None]] = {
    "period_not_loaded": ("rule", "period_not_loaded"),
    "sidecar_set_invalid": ("rule", "set_failure"),
    "adjusted_calendar_pair": ("rule", "adjusted_calendar_pair"),
    "measure_kind_unresolved": ("rule", "measure_kind_unresolved"),
    "member_weighted_count": ("rule", "member_weighted_count"),
    "needs_review": ("rule", "unverified_fact"),
    "annual_value_mean": ("rule", "annual_value_reading"),
    "statistic_term_differs": ("warn", "statistic_term_differs"),
    "imputed_version_averaged": ("note", None),
}


def flag_messages(facts: NodeFacts) -> tuple[list[Message], list[str]]:
    """The warnings and notes the node's flags give beyond the rules."""
    messages, notes = [], []
    for flag in facts.flag_text:
        code = flag_code(flag)
        text = flag.split(":", 1)[1].strip() if ":" in flag else flag
        kind, rule = FLAG_OUTCOMES.get(code, ("warn", "flag"))
        if kind == "warn":
            messages.append(Message(level="warn", rule=rule or code, text=text))
        elif kind == "note":
            notes.append(text)
    return messages, notes


# ── paths ────────────────────────────────────────────────────────────────────


def _step(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    if isinstance(value, list):
        if key == "*":
            return value
        if key.isdigit():
            i = int(key)
            return value[i] if i < len(value) else None
    return None


def lookup(path: str, node: Node, facts: NodeFacts, column: dict | None = None) -> Any:
    """A rule path's value, or None when absent. `columns.group_by.*` gives the
    list across grouping columns."""
    parts = path.split(".")
    root, rest = parts[0], parts[1:]
    if root not in ROOTS:
        raise UnknownPath(f"A rule path starts with {root!r}: {path}")
    if root == "flags":
        return list(facts.flags)
    if root == "column":
        if column is None:
            raise UnknownPath(f"{path} is read only by a rule with `each`.")
        value: Any = column
    elif root == "columns":
        if not rest:
            return facts.columns
        if rest[0] in INDEXED:
            index = rest[1] if len(rest) > 1 else "*"
            rest = rest[2:]
            if index == "*":
                found = [
                    c for k, c in facts.columns.items() if k.startswith(f"{parts[1]}.")
                ]
                return [_walk(c, rest) for c in found]
            value = facts.columns.get(f"{parts[1]}.{index}")
        else:
            value = facts.columns.get(rest[0])
            rest = rest[1:]
    else:
        value = {
            "asked": node.asked,
            "resolved": node.resolved,
            "master": facts.master,
            "derived": facts.derived,
        }[root]
    return _walk(value, rest)


def _walk(value: Any, rest: list[str]) -> Any:
    for key in rest:
        if value is None:
            return None
        value = _step(value, key)
    return value


def _empty(v: Any) -> bool:
    return v is None or v == [] or v == {} or v == ""


def holds(test: Test, value: Any) -> bool:
    op, want = test.op, test.value
    if op == "missing":
        return _empty(value)
    if op == "present":
        return not _empty(value)
    if op in ("has", "lacks"):
        members = (
            value if isinstance(value, list) else ([] if value is None else [value])
        )
        return (want in members) == (op == "has")
    values = value if isinstance(value, list) else [value]
    if not values:
        values = [None]
    if op == "eq":
        return any(v == want for v in values)
    if op == "ne":
        return any(v != want for v in values)
    if op == "in":
        return any(v in want for v in values)
    if op == "not_in":
        return any(v not in want for v in values)
    if op == "true":
        return any(v is True for v in values)
    if op == "false":
        return all(v is not True for v in values)
    raise UnknownPath(f"unknown test operation {op!r}")


# ── evaluation ───────────────────────────────────────────────────────────────


class _Format(string.Formatter):
    def get_value(self, key, args, kwargs):  # noqa: ANN001
        return kwargs.get(key, "")


def _text(value: Any) -> str | None:
    if _empty(value):
        return None
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _fire(rule: Rule, node: Node, facts: NodeFacts, column: dict | None) -> Message:
    prop = rule.property
    if prop and prop.split(".")[0] in ROOTS:
        prop = _text(lookup(prop, node, facts, column))
    if column is not None:
        name, table = column.get("column"), column.get("table")
    else:
        measure = facts.columns.get("measure") or {}
        name = measure.get("column")
        table = measure.get("table") or facts.derived.get("base_table")
    detail = _text(lookup(rule.detail, node, facts, column)) if rule.detail else None
    text = _Format().format(
        rule.message,
        column=name or "",
        property=prop or "",
        table=table or "",
        detail=detail or "not stated",
    )
    uses_column = column is not None or "{column}" in rule.message or rule.property
    return Message(
        level=rule.level,
        rule=rule.id,
        column=name if uses_column else None,
        property=(prop.split(", ")[0] if prop else None),
        text=text,
    )


def fired(
    rules: Sequence[Rule], node: Node, facts: NodeFacts
) -> list[tuple[Rule, Message]]:
    """Every rule that holds, with its message, in table order."""
    out = []
    for rule in rules:
        if rule.each is None:
            targets: list[dict | None] = [None]
        elif rule.each == "used":
            targets = [c for _, c in used_columns(facts)]
        else:
            targets = [c for _, c in grouping_columns(facts)]
        for column in targets:
            if all(holds(t, lookup(t.on, node, facts, column)) for t in rule.when):
                out.append((rule, _fire(rule, node, facts, column)))
    return out


NOTE_LABELS = {
    "time_basis": "time basis {value}",
    "missing_codes": "missing codes {value}",
    "imputation": "{value}",
    "count_unit": "count unit {value}",
}


def notes(node: Node) -> list[str]:
    """The choices the user did not state: the weight always, and every other
    entry whose source is defaulted."""
    out = []
    weight = node.resolved.get("weight")
    if weight is not None:
        out.append(f"weight {weight.get('value') or 'none'}: {weight.get('source')}")
    for key, label in NOTE_LABELS.items():
        entry = node.resolved.get(key) or {}
        if entry.get("source") == "defaulted":
            value = str(entry.get("value")).replace("_", " ")
            out.append(f"{label.format(value=value)}: defaulted")
    return out


def evaluate(
    node: Node,
    facts: NodeFacts,
    rules: Sequence[Rule],
    method: str | None,
    extra: Sequence[Message] = (),
    extra_notes: Sequence[str] = (),
) -> Verdict:
    """One verdict from the rules that hold: the worst level wins, warnings
    accumulate. A substitute is returned as is; `judge` re-checks it once."""
    found = fired(rules, node, facts)
    messages = [m for _, m in found] + list(extra)
    level = max((m.level for m in messages), key=LEVEL_ORDER.get, default="pass")
    weighted = (node.resolved.get("weight") or {}).get("value") is not None
    return Verdict(
        level=level,
        method=method,
        weighted=weighted,
        messages=messages,
        notes=notes(node) + list(extra_notes),
    )


def substitute_target(rules: Sequence[Rule], verdict: Verdict) -> str | None:
    by_id = {r.id: r for r in rules}
    for m in verdict.messages:
        if m.level == "substitute":
            return by_id[m.rule].target
    return None


# ── a node's verdict ─────────────────────────────────────────────────────────


def judge(node: Node, facts: NodeFacts, weight_column: str = "finlwt21") -> Verdict:
    """The verdict of an estimate node: the shared rules, the measure's rules
    and the flags; after a substitution, one re-check of the substituted node."""
    from ce_measures.registry import method_for, rules_for

    method = method_for(node)
    rules = rules_for(node)
    extra, extra_notes = flag_messages(facts)
    first = evaluate(node, facts, rules, method, extra, extra_notes)
    if first.level != "substitute":
        return first
    target = substitute_target(rules, first)
    if target != "weighted":  # the only substitution the tables hold
        return first
    weighted = node.model_copy(
        update={
            "resolved": node.resolved
            | {"weight": {"value": weight_column, "source": "substituted"}}
        }
    )
    facts2 = facts.model_copy(update={"derived": facts.derived | {"weighted": True}})
    again = evaluate(weighted, facts2, rules, method, extra, extra_notes)
    kept = [m for m in again.messages if m.level != "substitute"]  # no chaining
    level = "block" if any(m.level == "block" for m in kept) else "substitute"
    return Verdict(
        level=level,
        method=method,
        weighted=True,
        substituted_from=f"{method}, unweighted",
        messages=[m for m in first.messages if m.level == "substitute"] + kept,
        notes=again.notes,
    )
