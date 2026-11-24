"""The facts a node's rules read, gathered once (pipeline step 2).

Rules never open a sidecar: every `ce:` property of every column a node names
is copied here, with a few derived facts that need more than one lookup. Each
derived fact is worked out by one small function.
"""

from __future__ import annotations

import re
from typing import Any

from ce_capture.context import DETAILED_EXPENDITURE, MEMBER_ROWS
from ce_measures.models import Node, NodeFacts
from ce_meta.sidecars import TABLE_ID_PREFIX, Column, Report, SidecarSet, has_verify
from ce_meta.usage import usable_as

# A VERIFY value on one of these blocks: the method cannot be chosen (P3).
CORE_PROPERTIES = ("ce:role", "ce:scale", "ce:unit")
RATING_ORDER = {"reliable": 0, "limited": 1, "unsupported": 2}
GAP_YEAR = 2025
REGION = "region"


def _strip(table: str) -> str:
    return str(table).removeprefix(TABLE_ID_PREFIX)


def column_facts(sidecars: SidecarSet, table: str, name: str) -> dict[str, Any]:
    """A column's facts as a flat dict; `found` is false when no sidecar has it."""
    col: Column | None = sidecars.column(table, name)
    base: dict[str, Any] = {"table": table, "column": name}
    if col is None:
        return base | {"found": False, "tags": [], "needs_review": False}
    unverified = list(col.unverified)
    return base | {
        "found": True,
        "column": col.name,
        "business_name": col.business_name,
        "logical_type": col.logical_type,
        "tags": list(col.tags),
        "needs_review": col.needs_review,
        "verify_core": [p for p in unverified if p in CORE_PROPERTIES],
        "verify_other": [p for p in unverified if p not in CORE_PROPERTIES],
        "under_review": col.needs_review
        or any(p not in CORE_PROPERTIES for p in unverified),
        "review_property": "needs-review"
        if col.needs_review
        else next((p for p in unverified if p not in CORE_PROPERTIES), None),
        **col.facts,
    }


def column_refs(asked: dict) -> list[tuple[str, dict]]:
    refs = []
    if asked.get("measure"):
        refs.append(("measure", asked["measure"]))
    refs += [(f"group_by.{i}", r) for i, r in enumerate(asked.get("group_by") or [])]
    refs += [(f"filters.{i}", r) for i, r in enumerate(asked.get("filters") or [])]
    return refs


# ── flags ────────────────────────────────────────────────────────────────────


def flag_code(flag: str) -> str:
    return flag.split(":", 1)[0].strip()


def flag_names_node(flag: str, node: Node, columns: dict[str, dict]) -> bool:
    """Whether a flag concerns a node: it names the node's id, or a column the
    node uses; a flag that names neither concerns every node."""
    detail = flag.split(":", 1)[1] if ":" in flag else ""
    words = set(re.findall(r"[\w.]+", detail))
    ids = {w for w in words if re.fullmatch(r"n\d+", w)}
    refs = {w for w in words if "." in w}
    if ids:
        return node.id in ids
    if refs:
        used = {f"{c['table']}.{c['column']}" for c in columns.values()}
        return bool(refs & used)
    return True


# ── derived facts ────────────────────────────────────────────────────────────


def years(node: Node) -> list[int]:
    return [int(s["survey_year"]) for s in node.resolved.get("year_sets") or []]


def partitions(node: Node) -> list[tuple[int, str]]:
    out = []
    for s in node.resolved.get("year_sets") or []:
        for f in s.get("files") or []:
            out += [(int(f["survey_year"]), str(c)) for c in f["quarter_codes"]]
    return out


def geography_rating(columns: dict[str, dict], master: dict) -> str | None:
    """The worst master rating among the geography levels the node groups or
    filters by: a figure for one state rests on the same weights as a figure
    for each state."""
    ratings = master.get("ce:geographyLevels") or {}
    found = [
        ratings.get(c.get("ce:geographyLevel"))
        for key, c in columns.items()
        if key.startswith(("group_by.", "filters.")) and c.get("ce:geographyLevel")
    ]
    found = [r for r in found if r]
    return max(found, key=lambda r: RATING_ORDER.get(r, 0)) if found else None


def break_year(entry: dict) -> int | None:
    value = entry.get("from")
    try:
        return int(str(value)[:4])
    except (TypeError, ValueError):
        return None


def breaks_between(year_list: list[int], master: dict) -> list[str]:
    """The ids of the `ce:breaks` entries the years fall on both sides of."""
    if not year_list:
        return []
    low, high = min(year_list), max(year_list)
    return [
        str(b.get("id"))
        for b in master.get("ce:breaks") or []
        if (y := break_year(b)) is not None and low < y <= high
    ]


def item_code_unfiltered(node: Node, sidecars: SidecarSet) -> bool:
    """The measure's table has an item-code column and no filter names it."""
    measure = node.asked.get("measure")
    if not measure:
        return False
    table = sidecars.table(measure["table"])
    if table is None:
        return False
    codes = {c.name for c in table.columns.values() if c.get("ce:role") == "item_code"}
    if not codes:
        return False
    filtered = {
        f.get("column")
        for f in node.asked.get("filters") or []
        if f.get("table") == measure["table"]
    }
    return not codes & filtered


def row_represents(sidecars: SidecarSet, table: str) -> str | None:
    found = sidecars.table(table)
    return found.facts.get("ce:rowRepresents") if found else None


def named_tables(node: Node) -> list[str]:
    return sorted({str(r.get("table")) for _, r in column_refs(node.asked)})


def tables_without_route(node: Node, weight_table: str) -> list[str]:
    routed = {
        r.get("table")
        for r in (node.resolved.get("weight_route") or {}).get("value") or []
    }
    return [t for t in named_tables(node) if t != weight_table and t not in routed]


def grouping_tables(node: Node, base: str, weight_table: str) -> list[str]:
    """Tables of the grouping columns that are neither the weight table nor the
    table the node's rows come from: no join is defined for them yet."""
    return sorted(
        {
            str(r.get("table"))
            for r in node.asked.get("group_by") or []
            if r.get("table") not in (weight_table, base)
        }
    )


def base_table(node: Node, sidecars: SidecarSet, weight_table: str) -> str:
    """The table the node's rows come from (as Plan 4's `check.base_table`)."""
    measure = node.asked.get("measure")
    if measure:
        return str(measure.get("table"))
    if node.resolved.get("count_unit", {}).get("value") == "member":
        for t in sidecars.tables.values():
            if t.facts.get("ce:rowRepresents") == MEMBER_ROWS:
                return t.name
    return weight_table


def collect(
    node: Node,
    sidecars: SidecarSet,
    report: Report,
    pair_flags: list[str],
    loaded: set[tuple[int, str]] | None = None,
    check_errors: list[dict] | None = None,
) -> NodeFacts:
    """Every fact the node's rules read. `loaded` is the set of loaded partitions
    when it is known (a run); evaluating without data leaves it None."""
    master = dict(sidecars.master.facts) if sidecars.master else {}
    weight = master.get("ce:weight") or {}
    weight_table = _strip(weight.get("table", "fmli"))
    columns = {
        key: column_facts(sidecars, str(ref.get("table")), str(ref.get("column")))
        for key, ref in column_refs(node.asked)
    }
    flags = [f for f in pair_flags if flag_names_node(f, node, columns)]
    codes = [flag_code(f) for f in flags]
    tables = named_tables(node)
    year_list = years(node)
    not_loaded = (
        sorted({y for y, q in partitions(node) if (y, q) not in loaded})
        if loaded is not None
        else []
    )
    measure = node.asked.get("measure")
    measure_table = str(measure["table"]) if measure else None
    base = base_table(node, sidecars, weight_table)
    gap = (node.resolved.get("gap_2025") or {}).get("value")
    weighted = (node.resolved.get("weight") or {}).get("value") is not None
    set_failures = [f.detail for f in report.set_failures] + [
        f.split(":", 1)[1].strip()
        for f in flags
        if flag_code(f) == "sidecar_set_invalid"
    ]
    derived: dict[str, Any] = {
        "statistic": node.asked.get("statistic"),
        "weighted": weighted,
        "base_table": base,
        "geography_rating": geography_rating(columns, master),
        "breaks": breaks_between(year_list, master),
        "spans_break": bool(breaks_between(year_list, master)),
        "period_from_2025": any(y >= GAP_YEAR for y in year_list),
        "as_released_2025": GAP_YEAR in year_list and gap == "as_released",
        "has_grouping": bool(node.asked.get("group_by")),
        "has_filter": bool(node.asked.get("filters")),
        "item_code_unfiltered": item_code_unfiltered(node, sidecars),
        "measure_on_expn": bool(measure_table)
        and row_represents(sidecars, measure_table) == DETAILED_EXPENDITURE,
        "measure_level": (columns.get("measure") or {}).get("ce:level"),
        "measure_usable": bool(measure)
        and "measure"
        in usable_as(sidecars.column(measure["table"], measure["column"])),
        "tables_without_route": tables_without_route(node, weight_table),
        "grouping_tables": grouping_tables(node, base, weight_table),
        "set_failures": set_failures,
        "table_failures": [
            f"{t}: {f.detail}" for t in tables for f in report.table_failures(t)
        ],
        "period_not_loaded": "period_not_loaded" in codes or bool(not_loaded),
        "years_not_loaded": not_loaded,
        "check_errors": [e.get("message", str(e)) for e in check_errors or []],
        "within_year": bool(node.resolved.get("within_year")),
        "share": node.asked.get("statistic") == "share",
        "one_imputed_version": _imputed_one_version(node),
        "population_word": "the US population"
        if (node.resolved.get("count_unit") or {}).get("value") == "member"
        else "US households",
        "unresolved_why": _unresolved_why(flags),
        "interview_filter_tables": sorted(
            {
                str(f.get("table"))
                for f in (node.resolved.get("interview_filters") or {}).get("value")
                or []
                if f.get("table") != weight_table
            }
        ),
        "measure_table": measure_table,
    }
    for key, c in columns.items():
        if key.startswith("group_by."):
            # From 2025 Q1 region code C is how region is suppressed: the code C
            # warning says what the partial-suppression warning would.
            c["code_c"] = c["column"] == REGION and derived["period_from_2025"]
    return NodeFacts(
        columns=columns, master=master, flags=codes, flag_text=flags, derived=derived
    )


def _unresolved_why(flags: list[str]) -> str | None:
    for f in flags:
        if flag_code(f) == "measure_kind_unresolved":
            detail = f.split(":", 1)[1]
            return detail.split(":", 1)[1].strip() if ":" in detail else detail.strip()
    return None


def _imputed_one_version(node: Node) -> bool:
    """The user stated one imputed version (`stated.imputation` is version_N)."""
    choice = ((node.asked.get("stated") or {}).get("imputation") or {}).get("value")
    return isinstance(choice, str) and choice.startswith("version_")


def used_columns(facts: NodeFacts) -> list[tuple[str, dict]]:
    """Each distinct column the node uses, first mention kept."""
    seen: set[tuple[str, str]] = set()
    out = []
    for key, c in facts.columns.items():
        ident = (c["table"], c["column"])
        if ident not in seen:
            seen.add(ident)
            out.append((key, c))
    return out


def grouping_columns(facts: NodeFacts) -> list[tuple[str, dict]]:
    return [(k, c) for k, c in facts.columns.items() if k.startswith("group_by.")]


__all__ = ["collect", "column_facts", "flag_code", "has_verify", "used_columns"]
