"""The reply text (pipeline step 5). Code composes it; no model is called.

The text follows the user's own wording, one block per phrase in the request
record's `answers`, and is the same on every replay of the same records:

1. the phrase the user asked, as the block's heading;
2. what was computed, in plain words: the statistic, each column's business
   name with its stored name, the grouping, weighted or not, the time basis;
3. counts under their four fixed names, saying when households are counted as
   consumer units;
4. for a node on another table, the table, its row filters, and that an
   interview with no rows counts as zero;
5. the result per group, with its unit and standard error;
6. every warning of that node directly under its result;
7. a blocked node's reason in place of a result;

and the `says` statements close the text.
"""

from __future__ import annotations

import textwrap
from typing import Any

from ce_measures.measures.measure_count import NAMES as COUNT_NAMES
from ce_meta.sidecars import SidecarSet

WIDTH = 80
INDENT = "  "
PAIR_SUFFIXES = (
    ", current quarter",
    ", previous quarter",
    " this quarter",
    " last quarter",
)
BASIS = {"year": "per year", "quarter": "per quarter", "month": "per month"}
MAX_LABEL = 40
OPS = {"eq": "=", "ne": "≠", "lt": "<", "le": "≤", "gt": ">", "ge": "≥"}


def _wrap(text: str) -> list[str]:
    return textwrap.wrap(
        text,
        width=WIDTH,
        initial_indent=INDENT,
        subsequent_indent=INDENT,
        break_long_words=False,
        break_on_hyphens=False,
    )


def _lower_first(text: str) -> str:
    if len(text) > 1 and text[1].isupper():  # an acronym such as UCC
        return text
    return text[:1].lower() + text[1:]


def _upper_first(text: str) -> str:
    return text[:1].upper() + text[1:]


class _Describer:
    def __init__(self, request: dict, resolved: dict, sidecars: SidecarSet) -> None:
        self.asked = {n["id"]: n for n in request.get("nodes") or []}
        self.resolved = {n["id"]: n for n in resolved.get("nodes") or []}
        self.sidecars = sidecars

    def business(self, table: str, column: str, pair: bool = False) -> str:
        col = self.sidecars.column(table, column)
        name = (col.business_name if col else None) or column
        if pair:
            for suffix in PAIR_SUFFIXES:
                name = name.removesuffix(suffix)
        return _lower_first(name)

    def label(self, table: str, column: str, value: Any) -> str:
        """A code's short label; an item code, or a code whose label is a
        sentence, is shown as the code."""
        col = self.sidecars.column(table, column)
        if col is None or col.get("ce:role") == "item_code":
            return str(value)
        for code, label in col.codes:
            if str(code) == str(value) and label:
                short = label.split(" (")[0]
                return short if len(short) <= MAX_LABEL else str(value)
        return str(value)

    def measure(self, nid: str) -> str:
        asked, res = self.asked[nid], self.resolved[nid]
        m = asked["measure"]
        kind = (res.get("measure_kind") or {}).get("value")
        columns = (res.get("columns") or {}).get("value")
        if kind == "calendar_pair" and isinstance(columns, dict):
            cols = columns.get("2") or next(iter(columns.values()))
            name = self.business(m["table"], m["column"], pair=True)
        else:
            cols = columns or [m["column"]]
            name = self.business(m["table"], cols[0])
        text = f"{name} ({', '.join(cols)})"
        return text + ("," if "," in name else "")

    def filters(self, nid: str, table: str | None = None) -> str:
        parts = []
        for f in self.asked[nid].get("filters") or []:
            if table is not None and f["table"] != table:
                continue
            value = f["value"]
            values = value if isinstance(value, list) else [value]
            shown = ", ".join(self.label(f["table"], f["column"], v) for v in values)
            op = f["comparison"]
            if op == "in" and len(values) == 1 or op == "eq":
                text = f"{f['column']} {shown}"
            elif op == "in":
                text = f"{f['column']} in {shown}"
            elif op == "not_in":
                text = f"{f['column']} not in {shown}"
            elif op == "between":
                text = f"{f['column']} between {shown.replace(', ', ' and ')}"
            else:
                text = f"{f['column']} {OPS.get(op, op)} {shown}"
            parts.append(text)
        return " and ".join(parts)

    def grouping(self, nid: str) -> str:
        cols = [r["column"] for r in self.asked[nid].get("group_by") or []]
        return f" by {' and '.join(cols)}" if cols else ""

    def short(self, nid: str) -> str:
        """An estimate node in a few words, for a combine node's sentence."""
        asked = self.asked[nid]
        statistic = asked.get("statistic")
        if asked.get("kind") == "combine":
            return f"the {statistic} of node {nid}"
        if statistic == "count":
            method = (self.resolved[nid].get("count_method") or {}).get("value")
            what = _count_name(method)
            return f"the number of {what}{self.grouping(nid)}"
        filters = self.filters(nid)
        where = f" for {filters}" if filters else ""
        return f"the {statistic} of {self.measure(nid)}{where}{self.grouping(nid)}"

    def full(self, nid: str, asks: str, weighted: bool) -> str:
        asked, res = self.asked[nid], self.resolved[nid]
        statistic = asked.get("statistic")
        weight = "weighted" if weighted else "not weighted"
        if statistic == "count":
            method = (res.get("count_method") or {}).get("value")
            text = f"the number of {_count_name(method)}"
            if "household" in asks.lower() and method != "distinct_member":
                text += " (households are counted as consumer units)"
            filters = self.filters(nid)
            if filters:
                text += f" with {filters}"
            return f"{text}{self.grouping(nid)}, {weight}."
        text = self.short(nid).removesuffix(",")
        kind = (res.get("measure_kind") or {}).get("value")
        basis = (res.get("time_basis") or {}).get("value") or "year"
        if statistic == "mean":
            per = "per consumer unit"
            if kind != "interview_value":
                per += f" {BASIS.get(basis, 'per ' + basis)}"
            return f"{text}, {weight}, {per}."
        if kind == "interview_value":
            return f"{text}, {weight}."
        return f"{text}, {weight}, {BASIS.get(basis, 'per ' + basis)}."

    def table_note(self, nid: str) -> str | None:
        """How rows of a table other than fmli become one value per interview."""
        asked, res = self.asked[nid], self.resolved[nid]
        m = asked.get("measure")
        if not m or m["table"] == "fmli":
            return None
        filters = self.filters(nid, m["table"])
        rows = f"{m['table']} rows" + (f" with {filters}" if filters else "")
        reference = (res.get("reference_rows") or {}).get("value")
        if reference:
            rows += f" whose {reference['year_column']} is the survey year"
        route = next(
            (
                r
                for r in (res.get("weight_route") or {}).get("value") or []
                if r.get("table") == m["table"]
            ),
            {},
        )
        zero = (
            "; an interview with no such rows counts as zero"
            if route.get("child_rows_optional")
            else ""
        )
        return f"From {rows}, summed per interview{zero}."


def _count_name(method: str | None) -> str:
    fn = {
        "weighted_population": "do_weighted_count",
        "distinct_cuid": "do_distinct_count",
        "row_count": "do_row_count",
        "distinct_member": "do_distinct_member_count",
    }.get(method or "", "do_distinct_count")
    return COUNT_NAMES[fn]


def _number(value: float | None, unit: str | None, whole: bool = True) -> str:
    """A figure in its unit. `whole` is false for a mean of a count-valued
    column (vehicles owned, children): a mean of 1.93 is not 2."""
    if value is None:
        return "no value"
    if unit and unit.startswith("usd"):
        sign = "-" if value < 0 else ""
        return f"{sign}${abs(value):,.0f}"
    if unit == "count" and whole:
        return f"{value:,.0f}"
    if unit == "proportion":
        return f"{value:.3f}"
    return f"{value:,.2f}"


def _group_label(d: _Describer, nid: str, group: dict) -> str:
    if not group:
        return "All"
    refs = {r["column"]: r["table"] for r in d.asked[nid].get("group_by") or []}
    parts = []
    for column, value in group.items():
        shown = (
            "(missing)"
            if value is None
            else d.label(refs.get(column, "fmli"), column, value)
        )
        parts.append(f"{column} {shown}")
    return ", ".join(parts)


def _result_lines(d: _Describer, nid: str, result: dict) -> list[str]:
    unit = result.get("unit")
    whole = d.asked[nid].get("statistic") in ("count", "total")
    lines = [f"{INDENT}Result:"]
    for g in result.get("groups") or []:
        label = _group_label(d, nid, g["group"])
        text = f"{INDENT * 2}{label}: {_number(g['value'], unit, whole)}"
        if g.get("standard_error") is not None:
            se = _number(g["standard_error"], unit, whole)
            text += f" (standard error {se})"
        lines.append(text)
    removed = []
    if result.get("removed_missing"):
        removed.append(f"{result['removed_missing']:,} for a missing code or flag")
    if result.get("removed_null_mo_scope"):
        removed.append(f"{result['removed_null_mo_scope']:,} with no months in scope")
    if result.get("removed_null_value"):
        removed.append(f"{result['removed_null_value']:,} with no value")
    if result.get("dropped_no_rows"):
        removed.append(
            f"{result['dropped_no_rows']:,} with no rows in a required table"
        )
    if removed:
        lines += _wrap("Removed before computing: " + "; ".join(removed) + ".")
    return lines


def compose_reply(
    outcome: dict, request: dict, resolved: dict, sidecars: SidecarSet
) -> str:
    """The reply text of an outcome record (dumped), in the user's own words."""
    d = _Describer(request, resolved, sidecars)
    by_id = {n["id"]: n for n in outcome["nodes"]}
    run = outcome.get("mode") == "run"
    shown: list[str] = []
    blocks = []
    for answer in request.get("answers") or []:
        nid = answer["node"]
        node = by_id.get(nid)
        if node is None:
            continue
        lines = [_upper_first(answer["asks"])]
        if node["verdict"] == "block":
            for m in node["messages"]:
                if m["level"] == "block":
                    lines += _wrap(f"Cannot compute: {m['text']}")
            blocks.append("\n".join(lines))
            shown.append(nid)
            continue
        verb = "Computed" if run else "Will compute"
        asked = d.asked[nid]
        if asked.get("kind") == "combine":
            ids = [v for v in (asked.get("inputs") or {}).values() if v]
            words = ["the figure above" if i in shown else d.short(i) for i in ids[:2]]
            if asked.get("statistic") == "ratio":
                what = f"the ratio of {words[0]} to {words[1]}."
            else:
                what = f"the difference between {words[0]} and {words[1]}."
        else:
            what = d.full(nid, answer["asks"], node["weighted"])
        lines += _wrap(f"{verb}: {what}")
        note = d.table_note(nid) if asked.get("kind") == "estimate" else None
        if note:
            lines += _wrap(note)
        for m in node["messages"]:
            if m["level"] == "substitute":
                lines += _wrap(f"Changed: {m['text']}")
        if node.get("result"):
            lines += _result_lines(d, nid, node["result"])
        for m in node["messages"]:
            if m["level"] == "warn":
                lines += _wrap(f"Caution: {m['text']}")
        blocks.append("\n".join(lines))
        shown.append(nid)
    closing = "\n".join(f"{s}." for s in outcome.get("says") or [])
    return "\n\n".join([*blocks, closing]) + "\n"
