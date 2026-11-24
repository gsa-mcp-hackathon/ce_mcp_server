"""The sums every measure is made of, written once.

Every mean, total and weighted count is a sum, or a ratio of two sums, over the
interview frame. Expressions are SQL over the frame's columns with `{w}` for
the weight: `w` for the full weight, `r01`... for a replicate, `1.0` when the
estimate is unweighted (P4: every weight 1, the months-in-scope factor stays).
Nulls stay nulls; a group whose denominator is zero has a null value.
"""

from __future__ import annotations

import math
from dataclasses import replace

import duckdb
import numpy as np
import pyarrow as pa

from ce_measures import replicates as rep
from ce_measures.join import Frame, GroupColumn
from ce_measures.models import Estimate, GroupEstimate

UNWEIGHTED = "1.0"


def _sql(frame: pa.Table, sql: str) -> list[tuple]:
    con = duckdb.connect()
    try:
        con.register("frame", frame)
        return con.execute(sql).fetchall()
    finally:
        con.close()


def code(value) -> str | None:
    """A grouping value as a code: text as stored, a whole number without '.0'."""
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _group(groups: list[GroupColumn], values: tuple) -> dict[str, str | None]:
    return {g.column: code(v) for g, v in zip(groups, values, strict=True)}


def _key(groups: list[GroupColumn], values: tuple) -> tuple:
    return tuple(sorted(_group(groups, values).items()))


def clean(frame: Frame, needs_mo_scope: bool = True) -> Frame:
    """Drop the interviews a statistic must not see, counting each reason: a
    missing code or removing flag, no rows in a required table, a null
    `mo_scope` (only for a method that uses it), a null value."""
    mo = "mo_scope IS NULL" if needs_mo_scope else "false"
    keep = f"NOT drop_missing AND NOT no_rows AND NOT ({mo}) AND x IS NOT NULL"
    rows = _sql(
        frame.table,
        "SELECT count(*) FILTER (WHERE drop_missing), "
        "count(*) FILTER (WHERE NOT drop_missing AND no_rows), "
        f"count(*) FILTER (WHERE NOT drop_missing AND NOT no_rows AND {mo}), "
        f"count(*) FILTER (WHERE NOT drop_missing AND NOT no_rows AND NOT ({mo}) "
        "AND x IS NULL) FROM frame",
    )[0]
    con = duckdb.connect()
    try:
        con.register("frame", frame.table)
        kept = con.sql(f"SELECT * FROM frame WHERE {keep}").to_arrow_table()
    finally:
        con.close()
    return replace(
        frame,
        table=kept,
        removed_missing=frame.removed_missing + rows[0],
        dropped_no_rows=frame.dropped_no_rows + rows[1],
        removed_null_mo_scope=frame.removed_null_mo_scope + rows[2],
        removed_null_value=frame.removed_null_value + rows[3],
    )


def ratio_of_sums(
    frame: pa.Table,
    numerator: str,
    denominator: str | None,
    groups: list[GroupColumn],
    weight: str | None,
    x: str = "x",
) -> list[GroupEstimate]:
    """One estimate per group: Σnumerator / Σdenominator, or Σnumerator alone."""
    w = weight or UNWEIGHTED
    keys = ", ".join(g.alias for g in groups)
    den = f"SUM({denominator.format(w=w, x=x)})" if denominator else "NULL"
    sql = (
        f"SELECT {keys + ', ' if keys else ''}"
        f"SUM({numerator.format(w=w, x=x)}), {den}, "
        f"count(*), count(DISTINCT cuid) FROM frame"
    )
    if keys:
        sql += f" GROUP BY {keys} ORDER BY {keys} NULLS LAST"
    out = []
    n = len(groups)
    for row in _sql(frame, sql):
        num, d, rows, units = row[n:]
        if denominator is None:
            value = num
        else:
            value = None if num is None or not d else num / d
        out.append(
            GroupEstimate(
                group=_group(groups, row[:n]),
                value=value,
                interviews=rows,
                units=units,
            )
        )
    return out


def _variance(theta: float, theta_r: np.ndarray, R: int, k: float) -> float:  # noqa: N803
    return float(rep.variance(np.array([theta]), np.array([theta_r]), R, k)[0])


def _scaled(value: float | None, scale: float) -> float | None:
    return None if value is None else value * scale


def summarize(
    frame: Frame,
    numerator: str,
    denominator: str | None,
    weight: str | None,
    *,
    scale: float = 1.0,
    unit: str | None = None,
    variance: tuple[int, float] | None = None,
) -> Estimate:
    """An estimate from the cleaned frame, with replicate standard errors when
    `variance` (R, k) is given and the estimate is weighted. With five version
    columns the error is the multiple-imputation one."""
    found = ratio_of_sums(frame.table, numerator, denominator, frame.groups, weight)
    groups = [g.model_copy(update={"value": _scaled(g.value, scale)}) for g in found]
    counts = frame.counts()
    if variance is None or weight is None or not frame.replicates:
        return Estimate(groups=groups, unit=unit, **counts)
    R, k = variance  # noqa: N806
    aliases = [g.alias for g in frame.groups]
    reps = rep.estimates(frame.table, numerator, denominator, aliases, frame.replicates)
    keyed = {
        _key(frame.groups, values): theta * scale for values, theta in reps.items()
    }
    method = "replicate weights"
    errors: dict[tuple, float] = {}
    if frame.versions:
        method = "replicate weights, five imputed versions"
        per_version = []
        for v in frame.versions:
            values = {
                g.key: _scaled(g.value, scale)
                for g in ratio_of_sums(
                    frame.table, numerator, denominator, frame.groups, weight, x=v
                )
            }
            thetas = rep.estimates(
                frame.table, numerator, denominator, aliases, frame.replicates, x=v
            )
            variances = {}
            for vals, theta in thetas.items():
                key = _key(frame.groups, vals)
                if values.get(key) is not None and not np.isnan(theta).any():
                    variances[key] = _variance(values[key], theta * scale, R, k)
            per_version.append((values, variances))
        for g in groups:
            vals = [pv[0].get(g.key) for pv in per_version]
            vars_ = [pv[1].get(g.key) for pv in per_version]
            if all(v is not None for v in vals + vars_):
                _, total = rep.multiple_imputation(vals, vars_)
                errors[g.key] = math.sqrt(total)
    else:
        for g in groups:
            theta_r = keyed.get(g.key)
            if g.value is None or theta_r is None or np.isnan(theta_r).any():
                continue
            errors[g.key] = math.sqrt(_variance(g.value, theta_r, R, k))
    groups = [
        g.model_copy(update={"standard_error": errors.get(g.key)}) for g in groups
    ]
    est = Estimate(groups=groups, unit=unit, standard_error_method=method, **counts)
    return est.with_replicates({k_: list(v) for k_, v in keyed.items()}, variance)


def count(frame: Frame, how: str, unit: str = "count") -> Estimate:
    """An unweighted count per group: `rows`, distinct `units` (cuid), or
    distinct `members` (cuid, membno)."""
    expr = {
        "rows": "count(*)",
        "units": "count(DISTINCT cuid)",
        "members": "count(DISTINCT (cuid, membno))",
    }[how]
    keys = ", ".join(g.alias for g in frame.groups)
    sql = (
        f"SELECT {keys + ', ' if keys else ''}{expr}, count(*), "
        f"count(DISTINCT cuid) FROM frame"
    )
    if keys:
        sql += f" GROUP BY {keys} ORDER BY {keys} NULLS LAST"
    n = len(frame.groups)
    groups = [
        GroupEstimate(
            group=_group(frame.groups, row[:n]),
            value=float(row[n]),
            interviews=row[n + 1],
            units=row[n + 2],
        )
        for row in _sql(frame.table, sql)
    ]
    return Estimate(groups=groups, unit=unit, **frame.counts())


def average_versions(estimates: list[Estimate]) -> Estimate:
    """The mean of the five estimates of a table whose rows are the imputed
    versions (`itii`), with the multiple-imputation error when each has one."""
    first = estimates[0]
    by_key: dict[tuple, list[GroupEstimate]] = {}
    for est in estimates:
        for g in est.groups:
            by_key.setdefault(g.key, []).append(g)
    groups = []
    for g in first.groups:
        found = by_key.get(g.key, [])
        values = [x.value for x in found]
        if len(found) != len(estimates) or any(v is None for v in values):
            groups.append(g.model_copy(update={"value": None, "standard_error": None}))
            continue
        se = None
        errors = [x.standard_error for x in found]
        if all(e is not None for e in errors):
            mean, total = rep.multiple_imputation(values, [e * e for e in errors])
            se = math.sqrt(total)
        else:
            mean = float(np.mean(values))
        groups.append(g.model_copy(update={"value": mean, "standard_error": se}))
    method = first.standard_error_method
    return Estimate(
        groups=groups,
        unit=first.unit,
        removed_missing=sum(e.removed_missing for e in estimates),
        removed_null_mo_scope=first.removed_null_mo_scope,
        removed_null_value=first.removed_null_value,
        dropped_no_rows=first.dropped_no_rows,
        standard_error_method=f"{method}, five imputed versions" if method else None,
        notes=["the mean of the five imputed versions (impnum 1 to 5)"],
    )
