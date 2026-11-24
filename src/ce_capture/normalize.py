"""Normalize: map what a model wrote to stored names and codes (D26).

Pure code, run before the check. Each change is written to `fixes` as node,
field, from, to and rule, so what the model wrote stays recoverable. What
normalize cannot map it leaves as written, and the capture check reports it
with the nearest names. Normalize never judges method.
"""

from __future__ import annotations

import copy

from ce_capture.context import CaptureContext
from ce_capture.periods import quarters_in, years_in
from ce_capture.rules import load_phrases
from ce_capture.text import words

SET_COMPARISONS = frozenset({"eq", "ne", "in", "not_in"})


def _fix(fixes: list[dict], node: str | None, field: str, old, new, rule: str) -> None:
    fixes.append({"node": node, "field": field, "from": old, "to": new, "rule": rule})


def _ref(ref: dict, where: str, node: str, ctx: CaptureContext, fixes: list[dict]):
    """Stored table and column names for one column reference, in place."""
    table = ctx.table(str(ref.get("table", "")))
    if table is None:
        return None
    if table.name != ref["table"]:
        _fix(fixes, node, f"{where}.table", ref["table"], table.name, "table_name")
        ref["table"] = table.name
    column = table.column(str(ref.get("column", "")))
    if column is None:
        return None
    if column.name != ref["column"]:
        _fix(fixes, node, f"{where}.column", ref["column"], column.name, "column_name")
        ref["column"] = column.name
    return column


def code_for(
    value, codes: tuple[tuple[str, str | None], ...]
) -> tuple[str | None, str]:
    """The code a filter value stands for, and how it was found.

    Returns (code, rule): rule is "" when the value is a code as written,
    "filter_value_type" when only its type or case differed, "filter_label"
    when it was a label. (None, "") when it is neither, or a label that more
    than one code carries.
    """
    text = str(value)
    values = {v for v, _ in codes}
    if text in values:
        return text, ("" if isinstance(value, str) else "filter_value_type")
    if isinstance(value, float) and value.is_integer() and str(int(value)) in values:
        return str(int(value)), "filter_value_type"
    lowered = {v.lower(): v for v in values}
    if text.strip().lower() in lowered:
        return lowered[text.strip().lower()], "filter_value_type"
    wanted = words(text)
    by_label = [v for v, label in codes if label and words(label) == wanted]
    if len(by_label) == 1:
        return by_label[0], "filter_label"
    return None, ""


def _filter_values(flt: dict, where: str, node: str, codes, fixes: list[dict]) -> None:
    if not codes or flt.get("comparison") not in SET_COMPARISONS:
        return
    value = flt.get("value")
    many = isinstance(value, list)
    items = value if many else [value]
    mapped = []
    changed = False
    for item in items:
        code, rule = code_for(item, codes)
        if code is None or not rule:
            mapped.append(item)
            continue
        mapped.append(code)
        changed = True
        _fix(fixes, node, f"{where}.value", item, code, rule)
    if changed:
        flt["value"] = mapped if many else mapped[0]


def _period(period: dict, node: str, fixes: list[dict]) -> None:
    """Years and quarters read from the user's words win over the model's values."""
    text = period.get("text")
    kind = period.get("kind")
    if kind == "year":
        found = years_in(text)
        if found and found != period.get("values"):
            _fix(fixes, node, "period.values", period.get("values"), found, "period")
            period["values"] = found
    elif kind == "quarter":
        found = quarters_in(text)
        if found and found != period.get("values"):
            _fix(fixes, node, "period.values", period.get("values"), found, "period")
            period["values"] = found
    elif kind == "not_stated" and years_in(text):
        found = years_in(text)
        _fix(fixes, node, "period.kind", kind, "year", "period")
        _fix(fixes, node, "period.values", period.get("values"), found, "period")
        period["kind"], period["values"] = "year", found


def normalize(
    nodes: list[dict], answers: list[dict], ctx: CaptureContext
) -> tuple[list[dict], list[dict], list[str]]:
    """Normalized copies of the nodes, the fixes made, and the flags raised."""
    nodes = copy.deepcopy(nodes)
    fixes: list[dict] = []
    flags: list[str] = []
    phrases = load_phrases()

    for node in nodes:
        nid = node.get("id")
        if node.get("kind") == "estimate":
            if node.get("measure"):
                _ref(node["measure"], "measure", nid, ctx, fixes)
            for i, ref in enumerate(node.get("group_by") or []):
                _ref(ref, f"group_by.{i}", nid, ctx, fixes)
            for i, flt in enumerate(node.get("filters") or []):
                column = _ref(flt, f"filters.{i}", nid, ctx, fixes)
                if column is not None:
                    _filter_values(flt, f"filters.{i}", nid, column.codes, fixes)
            if node.get("period"):
                _period(node["period"], nid, fixes)

        # The model's statistic term stands; a disagreement with its words is
        # a flag for rule validation, never a change.
        asked = " ".join(a.get("asks", "") for a in answers if a.get("node") == nid)
        found, allowed = phrases.statistics_named(asked)
        if found and node.get("statistic") not in allowed:
            flags.append(
                f"statistic_term_differs: {nid} is {node.get('statistic')}; "
                f"the words say {', '.join(repr(p) for p in found)}"
            )
    return nodes, fixes, flags
