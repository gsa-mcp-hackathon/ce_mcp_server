"""The interview frame: one row per interview for a node (P13, pipeline step 4).

`build_sql` writes the one DuckDB query that gives a node its frame. It is a
pure function: table and column names come from the sidecar set and are
quoted, and every value from a record goes in as a parameter. `interview_frame`
runs it.

The table "Part of the node / Where it is applied", row by row:

- The year set's partitions filter every table; each row carries the survey
  year of its set (`set_year`).
- A filter on the measure's table, and the reference rows of a monthly table,
  apply to that table's rows before they are summed to the interview.
- A filter on `fmli` applies to the interviews.
- Each table of `interview_filters` becomes a set of interviews: those with at
  least one row matching every filter on that table (`exists`).
- A grouping on `fmli` applies to the interviews; a grouping on the measure's
  table gives one row per interview and group, with every interview in every
  group and zero where it has no rows.
- The weight, `mo_scope` and the replicate weights come from the `fmli` row.
  Rows join on the route's keys and on the partition columns, so the `1` and
  `1x` files never mix.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import duckdb
import pyarrow as pa

from ce_measures.errors import CeMeasuresError
from ce_measures.models import Node
from ce_meta.sidecars import TABLE_ID_PREFIX, SidecarSet

INTERVIEW_KEY = "newid"
UNIT_KEY = "cuid"
MEMBER_KEY = "membno"
PARTITION = ("survey_year", "quarter_code")
REMOVED_MEANINGS = frozenset({"skip_pattern", "parent_record"})
IMPUTATION_NUMBER = "impnum"

# The 2025 adjustment of the skill's estimation reference ("The 2025 collection
# gap"): mo_scope by interview month, and amounts scaled by reference month.
GAP_YEAR = 2025
ADJUSTED_MO_SCOPE = (
    ((2025, 8), 3.5),
    ((2025, 9), 5.5),
    ((2025, 12), 5.5),
    ((2026, 1), 3.5),
)
ADJUSTED_MONTH_SCALE = ((7, 1.5), (8, 3.0), (9, 3.0), (10, 1.5))
INTERVIEW_YEAR, INTERVIEW_MONTH = "qintrvyr", "qintrvmo"


def quote(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


@dataclass(frozen=True)
class GroupColumn:
    alias: str  # g0, g1, ...
    table: str
    column: str


@dataclass(frozen=True)
class FrameQuery:
    sql: str
    params: list[Any]
    removed_sql: str | None  # counts the measure-table rows removed before the sum
    removed_params: list[Any]
    groups: list[GroupColumn]
    versions: list[str]  # x_v1..x_v5 when the measure has five version columns
    optional_rows: bool


@dataclass(frozen=True)
class Frame:
    """A node's interview frame and what was removed on the way."""

    table: pa.Table
    groups: list[GroupColumn]
    versions: list[str] = field(default_factory=list)
    replicates: list[str] = field(default_factory=list)
    removed_missing: int = 0
    removed_null_mo_scope: int = 0
    removed_null_value: int = 0
    dropped_no_rows: int = 0
    member_rows: bool = False

    def counts(self) -> dict[str, int]:
        return {
            "removed_missing": self.removed_missing,
            "removed_null_mo_scope": self.removed_null_mo_scope,
            "removed_null_value": self.removed_null_value,
            "dropped_no_rows": self.dropped_no_rows,
        }


@dataclass
class _Sql:
    params: list[Any] = field(default_factory=list)

    def p(self, value: Any) -> str:
        self.params.append(value)
        return "?"

    def ps(self, values: list[Any]) -> str:
        return ", ".join(self.p(v) for v in values)


# ── pieces ───────────────────────────────────────────────────────────────────


def _strip(name: str) -> str:
    return str(name).removeprefix(TABLE_ID_PREFIX)


def _col(sidecars: SidecarSet, table: str, name: str) -> str:
    """A column's quoted stored name; a name in no sidecar is refused."""
    found = sidecars.column(table, name)
    if found is None:
        raise CeMeasuresError(f"{table}.{name} is in no sidecar.")
    return quote(found.name)


def _year_sets(node: Node) -> list[tuple[int, list[tuple[int, list[str]]]]]:
    out = []
    for s in node.resolved.get("year_sets") or []:
        files = [(int(f["survey_year"]), list(f["quarter_codes"])) for f in s["files"]]
        out.append((int(s["survey_year"]), files))
    if not out:
        raise CeMeasuresError(f"Node {node.id} has no year set.")
    return out


def _partition_of(alias: str, files: list[tuple[int, list[str]]], q: _Sql) -> str:
    parts = [
        f"({alias}.survey_year = {q.p(y)} AND {alias}.quarter_code IN ({q.ps(codes)}))"
        for y, codes in files
    ]
    return "(" + " OR ".join(parts) + ")"


def _all_partitions(alias: str, sets, q: _Sql) -> str:
    return "(" + " OR ".join(_partition_of(alias, files, q) for _, files in sets) + ")"


def _set_year(alias: str, sets, q: _Sql) -> str:
    whens = " ".join(
        f"WHEN {_partition_of(alias, files, q)} THEN {q.p(year)}"
        for year, files in sets
    )
    return f"CASE {whens} END"


def _condition(expr: str, comparison: str, value: Any, q: _Sql) -> str:
    """One filter as SQL. A text value compares the column as text, a number as
    a number, so the stored type does not matter."""
    values = value if isinstance(value, list) else [value]
    text = all(isinstance(v, str) for v in values)
    col = f"CAST({expr} AS VARCHAR)" if text else f"TRY_CAST({expr} AS DOUBLE)"
    if comparison in ("in", "not_in"):
        neg = "NOT " if comparison == "not_in" else ""
        return f"{col} {neg}IN ({q.ps(values)})"
    if comparison == "between":
        if len(values) != 2:
            raise CeMeasuresError("A between filter needs two values.")
        return f"{col} BETWEEN {q.p(values[0])} AND {q.p(values[1])}"
    ops = {"eq": "=", "ne": "<>", "lt": "<", "le": "<=", "gt": ">", "ge": ">="}
    if comparison not in ops:
        raise CeMeasuresError(f"Unknown comparison {comparison!r}.")
    return f"{col} {ops[comparison]} {q.p(values[0])}"


def _filters(
    alias: str, table: str, filters: list[dict], sidecars, q: _Sql
) -> list[str]:
    return [
        _condition(
            f"{alias}.{_col(sidecars, table, f['column'])}",
            f["comparison"],
            f["value"],
            q,
        )
        for f in filters
    ]


def _removed(
    alias: str, table: str, columns: list[str], sidecars, q: _Sql
) -> str | None:
    """Rows to remove before a statistic: a missing code on a used column, or a
    flag meaning a skip pattern or a parent record (the amount sits on other
    rows)."""
    found = sidecars.table(table)
    parts = []
    for name in columns:
        col = found.column(name) if found else None
        if col is None:
            continue
        codes = [str(c) for c in col.get("ce:missingCodes") or []]
        if codes:
            parts.append(
                f"CAST({alias}.{quote(col.name)} AS VARCHAR) IN ({q.ps(codes)})"
            )
        for flag in found.columns.values():
            if flag.get("ce:flagFor") != col.name:
                continue
            meanings = flag.get("ce:flagMeanings") or {}
            drop = [
                str(code)
                for code, meaning in meanings.items()
                if REMOVED_MEANINGS
                & set(meaning if isinstance(meaning, list) else [meaning])
            ]
            if drop:
                parts.append(
                    f"COALESCE(CAST({alias}.{quote(flag.name)} AS VARCHAR) "
                    f"IN ({q.ps(drop)}), false)"
                )
    return "(" + " OR ".join(parts) + ")" if parts else None


def _route(node: Node, table: str) -> dict:
    for r in (node.resolved.get("weight_route") or {}).get("value") or []:
        if r.get("table") == table:
            return r
    raise CeMeasuresError(f"{table} has no route to the weight.")


def _join_on(child: str, parent: str, route: dict) -> str:
    keys = route.get("keys") or {}
    pairs = list(
        zip(
            keys.get("child") or [INTERVIEW_KEY],
            keys.get("parent") or [INTERVIEW_KEY],
            strict=False,
        )
    )
    on = [f"{child}.{quote(c)} = {parent}.{quote(p)}" for c, p in pairs]
    on += [f"{child}.{p} = {parent}.{p}" for p in PARTITION]
    return " AND ".join(on)


def replicate_columns(sidecars: SidecarSet) -> list[str]:
    """The replicate weight columns, from the master's ce:replicateWeights."""
    fact = (
        sidecars.master.facts.get("ce:replicateWeights") if sidecars.master else None
    ) or {}
    prefix, count = fact.get("prefix", "wtrep"), int(fact.get("count", 44))
    return [f"{prefix}{i:02d}" for i in range(1, count + 1)]


# ── the query ────────────────────────────────────────────────────────────────


def build_sql(
    node: Node,
    sources: dict[str, str],
    sidecars: SidecarSet,
    *,
    weight: tuple[str, str] = ("fmli", "finlwt21"),
    replicates: list[str] | None = None,
    base: str | None = None,
    version: int | None = None,
    imputed_versions: list[str] | None = None,
) -> FrameQuery:
    """The query of a node's interview frame.

    Output columns: `newid`, `cuid`, `set_year`, the grouping columns `g0`...,
    `x`, `mo_scope`, `w`, the replicate weights `r01`..., `drop_missing`,
    `no_rows`, and `membno` for a frame of member rows. `version` selects one
    `impnum` of a table whose rows are the five imputed versions.
    """
    weight_table, weight_column = weight
    asked, resolved = node.asked, node.resolved
    measure = asked.get("measure")
    kind = (resolved.get("measure_kind") or {}).get("value")
    gap = (resolved.get("gap_2025") or {}).get("value")
    adjusted = gap == "adjusted" and kind == "monthly_rows"
    base = base or (measure["table"] if measure else weight_table)
    member_rows = not measure and base != weight_table
    sets = _year_sets(node)
    q = _Sql()
    W = weight_table  # noqa: N806

    filters = asked.get("filters") or []
    fmli_filters = [f for f in filters if f["table"] == weight_table]
    base_filters = [f for f in filters if f["table"] == base and base != weight_table]
    groups = [
        GroupColumn(f"g{i}", r["table"], r["column"])
        for i, r in enumerate(asked.get("group_by") or [])
    ]
    fmli_groups = [g for g in groups if g.table == weight_table]
    base_groups = [g for g in groups if g.table == base and base != weight_table]

    # ── the interviews
    sel = [
        f"f.{quote(INTERVIEW_KEY)} AS newid",
        f"f.{quote(UNIT_KEY)} AS cuid",
        "f.survey_year",
        "f.quarter_code",
        f"{_set_year('f', sets, q)} AS set_year",
    ]
    mo = "CAST(f.mo_scope AS DOUBLE)"
    gap_sets = [s for s in sets if s[0] == GAP_YEAR]
    if adjusted and gap_sets:
        whens = " ".join(
            f"WHEN TRY_CAST(f.{quote(INTERVIEW_YEAR)} AS INTEGER) = {y} "
            f"AND TRY_CAST(f.{quote(INTERVIEW_MONTH)} AS INTEGER) = {m} THEN {v}"
            for (y, m), v in ADJUSTED_MO_SCOPE
        )
        in_gap = _partition_of("f", gap_sets[0][1], q)
        mo = f"CASE WHEN {in_gap} THEN CASE {whens} ELSE {mo} END ELSE {mo} END"
    sel.append(f"{mo} AS mo_scope")
    sel.append(f"CAST(f.{_col(sidecars, W, weight_column)} AS DOUBLE) AS w")
    for i, r in enumerate(replicates or [], start=1):
        sel.append(f"COALESCE(CAST(f.{quote(r)} AS DOUBLE), 0) AS r{i:02d}")
    sel += [f"f.{_col(sidecars, W, g.column)} AS {g.alias}" for g in fmli_groups]

    drop = []
    fmli_x = None
    versions: list[str] = []
    if measure and base == weight_table:
        columns = resolved.get("columns", {}).get("value")
        if kind == "calendar_pair" and isinstance(columns, dict):
            whens, used = [], set()
            for code, cols in columns.items():
                used |= set(cols)
                total = " + ".join(
                    f"CAST(f.{_col(sidecars, W, c)} AS DOUBLE)" for c in cols
                )
                whens.append(f"WHEN f.quarter_code = {q.p(code)} THEN {total}")
            fmli_x = f"CASE {' '.join(whens)} END"
            sel.append(f"{fmli_x} AS x0")
            used_columns = sorted(used)
        else:
            name = (columns or [measure["column"]])[0]
            fmli_x = f"CAST(f.{_col(sidecars, W, name)} AS DOUBLE)"
            sel.append(f"{fmli_x} AS x0")
            used_columns = [name]
            for i, v in enumerate(imputed_versions or [], start=1):
                sel.append(f"CAST(f.{_col(sidecars, W, v)} AS DOUBLE) AS x_v{i}")
                versions.append(f"x_v{i}")
        missing = _removed("f", W, used_columns, sidecars, q)
        if missing:
            drop.append(missing)
    grouping_missing = _removed("f", W, [g.column for g in fmli_groups], sidecars, q)
    if grouping_missing:
        drop.append(grouping_missing)
    sel.append(f"{' OR '.join(drop) if drop else 'false'} AS drop_missing")

    where = [_all_partitions("f", sets, q)]
    where += _filters("f", W, fmli_filters, sidecars, q)
    ctes = [
        f"f AS (SELECT {', '.join(sel)} FROM {sources[W]} f "
        f"WHERE {' AND '.join(where)})"
    ]

    # ── the measure's table, summed to the interview
    removed_sql, removed_params = None, []
    optional = True
    final_x = "f.x0" if fmli_x else "1.0"
    joins, outer_sel, outer_where = [], [], []
    if base != weight_table:
        route = _route(node, base)
        optional = bool(route.get("child_rows_optional"))
        rq = _Sql()  # the same conditions again, for the removal count
        child_where, child_where_r = [], []
        for qq, target in ((q, child_where), (rq, child_where_r)):
            target.append(_all_partitions("c", sets, qq))
            target += _filters("c", base, base_filters, sidecars, qq)
            if version is not None:
                target.append(
                    f"CAST(c.{quote(IMPUTATION_NUMBER)} AS VARCHAR) = "
                    f"{qq.p(str(version))}"
                )
            rows = (resolved.get("reference_rows") or {}).get("value")
            if rows and measure:
                year_col = _col(sidecars, base, rows["year_column"])
                parts = [
                    f"({_partition_of('c', files, qq)} AND "
                    f"TRY_CAST(c.{year_col} AS INTEGER) = {qq.p(year)})"
                    for year, files in sets
                ]
                target.append("(" + " OR ".join(parts) + ")")
        removed = None
        if measure:
            removed = _removed("c", base, [measure["column"]], sidecars, q)
            removed_r = _removed("c", base, [measure["column"]], sidecars, rq)
            if removed_r:
                removed_sql = (
                    f"SELECT count(*) AS n FROM {sources[base]} c "
                    f"WHERE {' AND '.join(child_where_r)} AND {removed_r}"
                )
                removed_params = rq.params
        if removed:
            child_where.append(f"NOT {removed}")
        keys = (route.get("keys") or {}).get("child") or [INTERVIEW_KEY]
        key_sel = [f"c.{quote(k)}" for k in keys] + [f"c.{p}" for p in PARTITION]
        gsel = [f"c.{_col(sidecars, base, g.column)} AS {g.alias}" for g in base_groups]
        if member_rows:
            child_sel = [*key_sel, f"c.{quote(MEMBER_KEY)} AS membno", *gsel]
            ctes.append(
                f"m AS (SELECT {', '.join(child_sel)} FROM {sources[base]} c "
                f"WHERE {' AND '.join(child_where)})"
            )
            joins.append(f"JOIN m ON {_join_on('m', 'f', route)}")
            outer_sel.append("m.membno")
            outer_sel += [f"m.{g.alias}" for g in base_groups]
            final_x = "1.0"
        else:
            amount = (
                f"CAST(c.{_col(sidecars, base, measure['column'])} AS DOUBLE)"
                if measure
                else "1.0"
            )
            rows = (resolved.get("reference_rows") or {}).get("value") or {}
            if adjusted and rows.get("month_column") and rows.get("year_column"):
                month = _col(sidecars, base, rows["month_column"])
                year_col = _col(sidecars, base, rows["year_column"])
                scale = " ".join(f"WHEN {m} THEN {v}" for m, v in ADJUSTED_MONTH_SCALE)
                amount = (
                    f"{amount} * CASE WHEN TRY_CAST(c.{year_col} AS INTEGER) = "
                    f"{GAP_YEAR} THEN CASE TRY_CAST(c.{month} AS INTEGER) {scale} "
                    "ELSE 1 END ELSE 1 END"
                )
            group_by = ", ".join(str(i + 1) for i in range(len(key_sel) + len(gsel)))
            ctes.append(
                f"m AS (SELECT {', '.join(key_sel + gsel)}, SUM({amount}) AS x "
                f"FROM {sources[base]} c WHERE {' AND '.join(child_where)} "
                f"GROUP BY {group_by})"
            )
            on = _join_on("m", "f", route)
            if base_groups:
                aliases = ", ".join(g.alias for g in base_groups)
                ctes.append(f"g AS (SELECT DISTINCT {aliases} FROM m)")
                joins.append("CROSS JOIN g")
                on += "".join(
                    f" AND m.{g.alias} IS NOT DISTINCT FROM g.{g.alias}"
                    for g in base_groups
                )
                outer_sel += [f"g.{g.alias}" for g in base_groups]
            joins.append(f"LEFT JOIN m ON {on}")
            final_x = "COALESCE(m.x, 0)" if optional else "m.x"
            if not optional:
                outer_sel.append("m.x IS NULL AS no_rows")

    # ── interview filters: a set of interviews per table
    by_table: dict[str, list[dict]] = {}
    for f in (resolved.get("interview_filters") or {}).get("value") or []:
        if f["table"] != weight_table:  # a filter on fmli applies to the interviews
            by_table.setdefault(f["table"], []).append(f)
    for i, (table, fs) in enumerate(sorted(by_table.items())):
        alias = f"t{i}"
        route = _route(node, table)
        cond = [_all_partitions(alias, sets, q), _join_on(alias, "f", route)]
        cond += _filters(alias, table, fs, sidecars, q)
        outer_where.append(
            f"EXISTS (SELECT 1 FROM {sources[table]} {alias} "
            f"WHERE {' AND '.join(cond)})"
        )

    cols = ["f.newid", "f.cuid", "f.set_year"]
    cols += [f"f.{g.alias}" for g in fmli_groups]
    cols += outer_sel
    cols.append(f"{final_x} AS x")
    cols += [f"f.{v}" for v in versions]
    cols += ["f.mo_scope", "f.w"]
    cols += [f"f.r{i:02d}" for i in range(1, len(replicates or []) + 1)]
    cols.append("f.drop_missing")
    if not any(c.endswith(" AS no_rows") for c in cols):
        cols.append("false AS no_rows")
    sql = f"WITH {', '.join(ctes)} SELECT {', '.join(cols)} FROM f {' '.join(joins)}"
    if outer_where:
        sql += " WHERE " + " AND ".join(outer_where)
    return FrameQuery(
        sql, q.params, removed_sql, removed_params, groups, versions, optional
    )


def run_query(query: FrameQuery) -> tuple[pa.Table, int]:
    con = duckdb.connect()
    try:
        table = con.sql(query.sql, params=query.params).to_arrow_table()
        removed = 0
        if query.removed_sql:
            removed = int(
                con.execute(query.removed_sql, query.removed_params).fetchone()[0]
            )
        return table, removed
    finally:
        con.close()


def interview_frame(
    node: Node,
    sources: dict[str, str],
    sidecars: SidecarSet,
    **options: Any,
) -> Frame:
    """Run the frame query and return the frame with what it removed."""
    query = build_sql(node, sources, sidecars, **options)
    table, removed = run_query(query)
    return Frame(
        table=table,
        groups=query.groups,
        versions=query.versions,
        replicates=[
            f"r{i:02d}" for i in range(1, len(options.get("replicates") or []) + 1)
        ],
        removed_missing=removed,
        member_rows="membno" in table.column_names,
    )


def with_counts(frame: Frame, **counts: int) -> Frame:
    return replace(frame, **counts)
