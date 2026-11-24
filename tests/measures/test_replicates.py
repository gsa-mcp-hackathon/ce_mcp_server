"""Replicate-weight standard errors: the checks of "How it is checked".

The fifth check (plain SQL on the 2025 fmli data) is in test_on_data.py.
"""

import numpy as np
import pyarrow as pa
import pytest

from ce_measures import replicates as rep
from ce_measures.errors import CeMeasuresError
from ce_measures.join import Frame
from ce_measures.measures.base import summarize
from tests.measures.conftest import tiny_docs

# Two units, a full year each: the mean of 10 and 20 is 15. Under the four
# replicates the means are 10, 20, 15 and 12.5, so Σ(θr − θ)² = 56.25.
HAND = pa.table(
    {
        "cuid": ["A", "B"],
        "x": [10.0, 20.0],
        "w": [1.0, 1.0],
        "mo_scope": [12.0, 12.0],
        "r01": [2.0, 0.0],
        "r02": [0.0, 2.0],
        "r03": [1.0, 1.0],
        "r04": [1.5, 0.5],
    }
)
REPS = ["r01", "r02", "r03", "r04"]
PER_YEAR = ("{x} * {w}", "{w} * mo_scope / 12.0")


def _frame(table=HAND, versions=()) -> Frame:
    return Frame(table=table, groups=[], replicates=REPS, versions=list(versions))


def test_check_1_a_hand_computed_fixture():
    theta_r = rep.estimates(HAND, *PER_YEAR, [], REPS)[()]
    assert list(theta_r) == [10.0, 20.0, 15.0, 12.5]
    assert rep.variance(np.array([15.0]), np.array([theta_r]), 4, 0.0)[0] == 56.25 / 4
    est = summarize(_frame(), *PER_YEAR, "w", variance=(4, 0.0))
    assert est.groups[0].value == 15.0
    assert est.groups[0].standard_error == pytest.approx(3.75)


def test_check_2_replicates_equal_to_the_weight_give_exactly_zero():
    table = HAND
    for r in REPS:
        table = table.set_column(table.schema.get_field_index(r), r, table["w"])
    est = summarize(_frame(table), *PER_YEAR, "w", variance=(4, 0.5))
    assert est.groups[0].standard_error == 0.0


def test_check_3_fay_k_one_half_gives_exactly_twice_brr():
    brr = summarize(_frame(), *PER_YEAR, "w", variance=(4, 0.0)).groups[0]
    fay = summarize(_frame(), *PER_YEAR, "w", variance=(4, 0.5)).groups[0]
    assert fay.standard_error == 2 * brr.standard_error


def test_check_5_five_identical_versions_give_the_single_version_error():
    table = HAND
    for v in range(1, 6):
        table = table.append_column(f"x_v{v}", table["x"])
    single = summarize(_frame(), *PER_YEAR, "w", variance=(4, 0.5)).groups[0]
    five = summarize(
        _frame(table, [f"x_v{v}" for v in range(1, 6)]),
        *PER_YEAR,
        "w",
        variance=(4, 0.5),
    ).groups[0]
    assert five.standard_error == pytest.approx(single.standard_error, abs=0)
    mean, total = rep.multiple_imputation([15.0] * 5, [2.0] * 5)
    assert (mean, total) == (15.0, 2.0)  # no between-version part


def test_multiple_imputation_adds_the_between_version_part():
    mean, total = rep.multiple_imputation([1.0, 2.0, 3.0, 4.0, 5.0], [1.0] * 5)
    assert mean == 3.0 and total == pytest.approx(1.0 + 1.2 * 2.5)
    with pytest.raises(CeMeasuresError):
        rep.multiple_imputation([1.0], [1.0])


def test_parameters_come_from_the_master_by_release():
    master = {
        p["property"]: p["value"]
        for p in tiny_docs()["ce-interview-master.odcs.yaml"]["customProperties"]
    }
    assert rep.parameters(master, 2025) == (44, 0.5)
    assert rep.parameters(master, 2026) == (44, 0.5)
    assert rep.parameters(master, 2024) == (44, 0.0)
    with pytest.raises(CeMeasuresError):
        rep.parameters({"ce:variance": {"byRelease": []}}, 2025)


def test_an_unweighted_estimate_has_no_standard_error():
    est = summarize(_frame(), *PER_YEAR, None, variance=(4, 0.5))
    assert est.groups[0].standard_error is None and est.standard_error_method is None
