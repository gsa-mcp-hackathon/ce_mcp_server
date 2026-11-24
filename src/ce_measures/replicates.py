"""Replicate-weight standard errors (P2), the one place NumPy is used.

A weighted estimate is re-run with each replicate weight in place of the full
weight, in the numerator and the denominator, and

    variance = Σ(θr − θ)² / (R · (1 − k)²)

with R and k from the master's `ce:variance` for the release: Fay's method
(k = 0.5) from 2025, standard BRR (k = 0) through 2024. A null replicate
weight counts as 0 (the frame query does this). Imputed income combines its
five versions as within-version variance plus (1 + 1/5) times the
between-version variance.
"""

from __future__ import annotations

import re

import duckdb
import numpy as np
import pyarrow as pa

from ce_measures.errors import CeMeasuresError

VERSIONS = 5


def parameters(master: dict, release: int) -> tuple[int, float]:
    """(R, k) of `ce:variance` for one release year; never a constant in code."""
    fact = master.get("ce:variance") or {}
    for entry in fact.get("byRelease") or []:
        rule = str(entry.get("releases", "")).replace(" ", "")
        m = re.fullmatch(r"(<=|>=|<|>|=)?(\d{4})", rule)
        if not m:
            continue
        op, year = m.group(1) or "=", int(m.group(2))
        ok = {
            "<=": release <= year,
            ">=": release >= year,
            "<": release < year,
            ">": release > year,
            "=": release == year,
        }[op]
        if ok:
            return int(entry["R"]), float(entry["k"])
    raise CeMeasuresError(f"The master gives no variance parameters for {release}.")


def variance(theta: np.ndarray, theta_r: np.ndarray, R: int, k: float) -> np.ndarray:  # noqa: N803
    """Σ(θr − θ)² / (R·(1 − k)²), per group: `theta` is (groups,), `theta_r`
    is (groups, replicates)."""
    theta = np.asarray(theta, dtype=float)
    theta_r = np.asarray(theta_r, dtype=float)
    return np.sum((theta_r - theta[:, None]) ** 2, axis=1) / (R * (1.0 - k) ** 2)


def estimates(
    frame: pa.Table,
    numerator: str,
    denominator: str | None,
    groups: list[str],
    weights: list[str],
    x: str = "x",
) -> dict[tuple, np.ndarray]:
    """Per group (keyed by the grouping values), the estimate under each
    replicate weight: numerator / denominator sums, or the numerator sum alone.
    `numerator` and `denominator` are SQL over the frame with `{w}` for the
    weight and `{x}` for the value. One query for all replicates."""
    sums = []
    for i, r in enumerate(weights):
        sums.append(f"SUM({numerator.format(w=r, x=x)}) AS n{i}")
        if denominator is not None:
            sums.append(f"SUM({denominator.format(w=r, x=x)}) AS d{i}")
    keys = ", ".join(groups)
    sql = f"SELECT {keys + ', ' if keys else ''}{', '.join(sums)} FROM frame"
    if keys:
        sql += f" GROUP BY {keys}"
    con = duckdb.connect()
    try:
        con.register("frame", frame)
        rows = con.execute(sql).fetchall()
    finally:
        con.close()
    out = {}
    g = len(groups)
    for row in rows:
        values = row[g:]
        if denominator is None:
            theta = np.array([np.nan if v is None else v for v in values], dtype=float)
        else:
            n = np.array(
                [np.nan if v is None else v for v in values[0::2]], dtype=float
            )
            d = np.array(
                [np.nan if v is None else v for v in values[1::2]], dtype=float
            )
            with np.errstate(divide="ignore", invalid="ignore"):
                theta = np.where(d == 0, np.nan, n / d)
        out[tuple(row[:g])] = theta
    return out


def multiple_imputation(
    values: list[float], variances: list[float]
) -> tuple[float, float]:
    """`do_mi_standard_error`: the mean of the version estimates, and the total
    variance: mean within-version variance + (1 + 1/m) × between-version variance."""
    v = np.asarray(values, dtype=float)
    u = np.asarray(variances, dtype=float)
    m = len(v)
    if m < 2:
        raise CeMeasuresError("Multiple imputation needs at least two versions.")
    within = float(np.mean(u))
    between = float(np.sum((v - v.mean()) ** 2) / (m - 1))
    return float(v.mean()), within + (1 + 1 / m) * between
