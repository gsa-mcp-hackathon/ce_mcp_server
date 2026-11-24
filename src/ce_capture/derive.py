"""Derivation: how each statistic of a captured request will be computed (D5).

Reads one request record with status captured and writes one resolved record.
Every entry names its source: stated (the user's words), defaulted (the
default set, `data/defaults.yaml`) or derived (read from a sidecar). It runs
again later, under new defaults or new sidecars, and each run writes a new
resolved record. Derivation judges no method: that is rule validation (Plan 5).
"""

from __future__ import annotations

from ce_capture.check import GAP_YEAR, base_table, column_refs
from ce_capture.context import (
    DETAILED_EXPENDITURE,
    MEMBER_ROWS,
    MONTHLY_ROWS,
    CaptureContext,
)
from ce_capture.periods import period_years
from ce_capture.rules import load_defaults
from ce_meta.catalog import SURVEY
from ce_meta.service import imputed_set
from ce_meta.sidecars import Column, has_verify
from ce_store import survey_year_set

# previous-quarter and current-quarter name endings, as the sidecar rules pair them
PAIR_ENDINGS = (("pq", "cq"), ("px4", "cx4"), ("p", "c"))
SPAN_UNIT = {"year": "usd_annual", "quarter": "usd_quarterly", "month": "usd_monthly"}
UNIT_SPAN = {v: k for k, v in SPAN_UNIT.items()}
SUMMARIZED = frozenset({"mean", "total"})
COUNTED = frozenset({"count", "share"})
LOADER_PERIODS = frozenset({"survey_year", "quarter", "quarter_code", "qyear"})


def _entry(value, source: str) -> dict:
    return {"value": value, "source": source}


def year_files(year: int) -> list[dict]:
    """The files of one survey year's set, grouped by the year they are stored in."""
    by_year: dict[int, list[str]] = {}
    for stored_year, code in survey_year_set(SURVEY, year):
        by_year.setdefault(stored_year, []).append(code)
    return [{"survey_year": y, "quarter_codes": codes} for y, codes in by_year.items()]


def measure_kind(
    table: str, column: Column | None, ctx: CaptureContext
) -> tuple[str, str | None]:
    """The kind of a measure, or ("unresolved", why)."""
    if column is None:
        return "unresolved", "the column is not in its sidecar"
    if not column.get("ce:role"):
        return "unresolved", "the column has no ce:role"
    if ctx.row_represents(table) == DETAILED_EXPENDITURE:
        return (
            "unresolved",
            "no estimation method is defined for detailed expenditure tables yet",
        )
    unit = column.get("ce:unit")
    if unit is None or has_verify(unit):
        return "unresolved", "its ce:unit is VERIFY"
    if column.get("ce:calendarPair"):
        return "calendar_pair", None
    if ctx.row_represents(table) in MONTHLY_ROWS:
        return "monthly_rows", None
    if unit == "usd_annual":
        return "annual_value", None
    return "interview_value", None


def pair_columns(name: str, partner: str) -> tuple[str, str] | None:
    """(previous-quarter, current-quarter) columns of a pair."""
    for prev, cur in PAIR_ENDINGS:
        if name.endswith(prev) and partner.endswith(cur):
            if name[: -len(prev)] == partner[: -len(cur)]:
                return name, partner
        if name.endswith(cur) and partner.endswith(prev):
            if name[: -len(cur)] == partner[: -len(prev)]:
                return partner, name
    return None


def _calendar_columns(column: Column) -> dict | None:
    pair = pair_columns(column.name, str(column.get("ce:calendarPair")))
    if pair is None:
        return None
    prev, cur = pair
    return {
        "1x": [cur],
        "2": [prev, cur],
        "3": [prev, cur],
        "4": [prev, cur],
        "1": [prev],
    }


def _reference_rows(table: str, years: list[int], ctx: CaptureContext) -> dict | None:
    found = ctx.table(table)
    if found is None:
        return None
    periods = [
        c.name
        for c in found.columns.values()
        if c.get("ce:role") == "period" and c.name not in LOADER_PERIODS
    ]
    year_col = next((c for c in periods if "yr" in c), None)
    month_col = next((c for c in periods if "mo" in c), None)
    if year_col is None:
        return None
    return {"year_column": year_col, "month_column": month_col, "years": years}


def _route(table: str, ctx: CaptureContext) -> dict | None:
    rel = ctx.route(table)
    if rel is None:
        return None
    keys = rel.get("keys") or {}
    return {
        "table": table,
        "parent": str(rel.get("parent", "")).removeprefix("ce-interview-"),
        "keys": {
            "parent": list(keys.get("parent") or []),
            "child": list(keys.get("child") or []),
        },
        "child_rows_optional": bool(rel.get("childRowsOptional")),
    }


class _Deriver:
    def __init__(self, record: dict, ctx: CaptureContext) -> None:
        self.record = record
        self.ctx = ctx
        self.defaults = load_defaults()
        self.flags: list[str] = []
        self.units: dict[str, str | None] = {}  # node -> unit of its result
        self.errors: dict[str, bool] = {}  # node -> has a standard error
        estimates = [
            n for n in record.get("nodes") or [] if n.get("kind") == "estimate"
        ]
        self.record_gap = next(
            (
                n["stated"]["gap_2025"]
                for n in estimates
                if (n.get("stated") or {}).get("gap_2025")
            ),
            None,
        )

    # ── an estimate node ─────────────────────────────────────────────────────

    def estimate(self, node: dict) -> dict:
        ctx, nid = self.ctx, node["id"]
        stated = node.get("stated") or {}
        statistic = node["statistic"]
        period = node.get("period") or {}
        years = period_years(period, ctx.release)
        # the set is computed from the period: derived, or defaulted with no period
        period_source = "defaulted" if period.get("kind") == "not_stated" else "derived"
        out: dict = {
            "id": nid,
            "kind": "estimate",
            "year_sets": [
                {"survey_year": y, "files": year_files(y), "source": period_source}
                for y in years
            ],
        }
        if period.get("kind") == "quarter":
            out["within_year"] = _entry({"quarters": period.get("values")}, "stated")
            self.flags.append(
                f"part_year_period: {nid} asks for "
                + ", ".join(map(str, period["values"]))
            )
        elif period.get("kind") == "month_range":
            out["within_year"] = _entry({"months": period.get("values")}, "stated")
            self.flags.append(
                f"part_year_period: {nid} asks for months "
                + " to ".join(map(str, period["values"]))
            )

        base = base_table(node, ctx)
        measure = node.get("measure") or None
        column = ctx.column(measure["table"], measure["column"]) if measure else None
        kind = None
        if statistic in SUMMARIZED and measure:
            kind, why = measure_kind(measure["table"], column, ctx)
            out["measure_kind"] = _entry(kind, "derived")
            if why:
                self.flags.append(
                    f"measure_kind_unresolved: {nid} "
                    f"{measure['table']}.{measure['column']}: {why}"
                )
            out["columns"] = self._columns(node, measure, column, kind, stated, out)
            if kind == "monthly_rows":
                rows = _reference_rows(measure["table"], years, ctx)
                if rows:
                    out["reference_rows"] = _entry(rows, "derived")

        routes = [
            r
            for t in sorted(
                {base} | {str(ref.get("table")) for _, ref in column_refs(node)}
            )
            if t != ctx.weight[0] and (r := _route(t, ctx))
        ]
        if routes:
            out["weight_route"] = _entry(routes, "derived")
        other = [
            {k: f[k] for k in ("table", "column", "comparison", "value")}
            for f in node.get("filters") or []
            if f.get("table") != base
        ]
        if other:
            out["interview_filters"] = _entry(other, "derived")

        # the weight
        weighted = stated.get("weighted")
        weight_column = ctx.weight[1]
        if weighted is not None:
            out["weight"] = _entry(
                weight_column if weighted["value"] else None, "stated"
            )
        else:
            out["weight"] = _entry(weight_column, "defaulted")
        is_weighted = out["weight"]["value"] is not None

        if statistic in SUMMARIZED and measure:
            basis = stated.get("time_basis")
            time_basis = basis["value"] if basis else self.defaults["time_basis"].value
            out["time_basis"] = _entry(time_basis, "stated" if basis else "defaulted")
            unit = column.get("ce:unit") if column is not None else None
            if kind in ("interview_value", "unresolved"):
                result = None if kind == "unresolved" else unit
            else:
                result = SPAN_UNIT[time_basis]
            out["unit"] = {"measure": unit, "result": result, "source": "derived"}
            self.units[nid] = result
            missing = stated.get("missing_codes")
            out["missing_codes"] = _entry(
                missing["value"] if missing else self.defaults["missing_codes"].value,
                "stated" if missing else "defaulted",
            )
            if statistic == "mean" and kind in ("annual_value", "interview_value"):
                self.flags.append(
                    f"annual_value_mean: {nid} uses the months-in-scope weighted "
                    "average, the project's reading of the BLS guide"
                )

        se = stated.get("standard_error")
        if se is not None:
            out["standard_error"] = _entry(se["value"], "stated")
        elif is_weighted:
            out["standard_error"] = _entry(True, "defaulted")
        else:
            out["standard_error"] = _entry(False, "derived")
        self.errors[nid] = bool(out["standard_error"]["value"])

        if statistic in COUNTED:
            unit_choice = stated.get("count_unit")
            count_unit = (
                unit_choice["value"]
                if unit_choice
                else self.defaults["count_unit"].value
            )
            out["count_unit"] = _entry(
                count_unit, "stated" if unit_choice else "defaulted"
            )
            if statistic == "count":
                out["count_method"] = _entry(
                    self._count_method(nid, count_unit, is_weighted), "derived"
                )
                self.units[nid] = "count"
            else:
                self.units[nid] = "proportion"

        levels = [
            level
            for ref in node.get("group_by") or []
            if (c := ctx.column(ref["table"], ref["column"]))
            and (level := c.get("ce:geographyLevel"))
        ]
        if levels:
            out["geography_level"] = _entry(levels[0], "derived")

        if GAP_YEAR in years:
            gap = stated.get("gap_2025") or self.record_gap
            if gap is not None:
                out["gap_2025"] = _entry(gap["value"], "stated")
                if gap["value"] == "adjusted" and kind == "calendar_pair":
                    self.flags.append(
                        f"adjusted_calendar_pair: {nid} "
                        f"{measure['table']}.{measure['column']}"
                    )
        return out

    def _columns(self, node, measure, column, kind, stated, out) -> dict:
        nid = node["id"]
        if kind == "calendar_pair" and column is not None:
            pair = _calendar_columns(column)
            if pair:
                return _entry(pair, "derived")
        chosen = measure["column"]
        table = self.ctx.table(measure["table"])
        imputed = imputed_set(table, column) if table and column else None
        choice = stated.get("imputation")
        is_itii = (
            self.ctx.row_represents(measure["table"]) == "imputed_income_item_month"
        )
        if imputed or is_itii or (column is not None and column.get("ce:imputed")):
            if choice:
                out["imputation"] = _entry(choice["value"], "stated")
                version = choice["value"].removeprefix("version_")
                if imputed and imputed.versions and version.isdigit():
                    chosen = imputed.versions[int(version) - 1]
                elif (
                    imputed
                    and imputed.mean
                    and choice["value"] == "five_versions_averaged"
                ):
                    chosen = imputed.mean
            else:
                out["imputation"] = _entry(
                    self.defaults["imputation"].value, "defaulted"
                )
                if imputed and imputed.mean and chosen in imputed.versions:
                    self.flags.append(
                        f"imputed_version_averaged: {nid} named {chosen}; resolved to "
                        f"{imputed.mean}, the mean of the five versions"
                    )
                    chosen = imputed.mean
        return _entry([chosen], "derived")

    def _count_method(self, nid: str, unit: str, weighted: bool) -> str:
        if weighted:
            if unit == "member":
                self.flags.append(f"member_weighted_count: {nid}")
            return "weighted_population"
        if unit == "interview":
            return "row_count"
        if unit == "member":
            return "distinct_member"
        return "distinct_cuid"

    # ── a combine node ───────────────────────────────────────────────────────

    def combine(self, node: dict) -> dict:
        nid = node["id"]
        keys = (
            ("numerator", "denominator")
            if node["statistic"] == "ratio"
            else ("first", "second")
        )
        inputs = {
            k: {"node": node["inputs"][k], "unit": self.units.get(node["inputs"][k])}
            for k in keys
        }
        (k1, i1), (k2, i2) = inputs.items()
        u1, u2 = i1["unit"], i2["unit"]
        conversion = "none"
        if u1 != u2 and u1 in UNIT_SPAN and u2 in UNIT_SPAN:
            conversion = f"{k2} from {u2} to {u1}"
            u2 = u1
        if node["statistic"] == "ratio":
            result = (
                "proportion" if u1 == u2 else (f"{u1} per {u2}" if u1 and u2 else None)
            )
        else:
            result = u1 if u1 == u2 else None
        self.units[nid] = result
        has_se = all(self.errors.get(i["node"], False) for i in inputs.values())
        self.errors[nid] = has_se
        return {
            "id": nid,
            "kind": "combine",
            "statistic": node["statistic"],
            "inputs": inputs,
            "conversion": _entry(conversion, "derived"),
            "standard_error": _entry(has_se, "derived"),
            "unit": {"result": result, "source": "derived"},
        }


def derive(
    record: dict, ctx: CaptureContext, resolved_id: str, derived_at: str
) -> dict:
    """The resolved record of a captured request record."""
    d = _Deriver(record, ctx)
    nodes = record.get("nodes") or []
    done: dict[str, dict] = {}
    for node in nodes:
        if node.get("kind") == "estimate":
            done[node["id"]] = d.estimate(node)
    pending = [n for n in nodes if n.get("kind") == "combine"]
    while (
        pending
    ):  # a combine of combines waits for its inputs; the check ruled out cycles
        ready = [
            n for n in pending if all(v in done for v in n["inputs"].values() if v)
        ]
        if not ready:
            break
        for node in ready:
            done[node["id"]] = d.combine(node)
            pending.remove(node)
    return {
        "resolved_id": resolved_id,
        "request_id": record["request_id"],
        "derived_at": derived_at,
        "defaults_version": d.defaults.version,
        "sidecars": record["sidecars"],
        "nodes": [done[n["id"]] for n in nodes if n["id"] in done],
        "flags": d.flags,
    }


__all__ = ["MEMBER_ROWS", "derive", "measure_kind", "pair_columns", "year_files"]
