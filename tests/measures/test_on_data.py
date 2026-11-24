"""Checks against plain SQL on the loaded 2025 data (`data/parquet`), marked slow.

The plain SQL is written from the skill's estimation reference ("Worked SQL"),
independently of `join.py`, and must give the same figures for every group.
"""

from __future__ import annotations

import math
from pathlib import Path

import duckdb
import pytest

from ce_capture.context import CaptureContext
from ce_capture.storage import JsonFileStore
from ce_mcp_server.service import build_service
from ce_measures.process import RequestProcessor
from ce_meta.config import MetaSettings
from ce_store import duckdb_source, survey_year_filter
from tests.measures.conftest import captured, estimate

ROOT = Path(__file__).parents[2]
DATA = ROOT / "data" / "parquet"
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not (DATA / "intrvw" / "fmli").exists(), reason="no loaded data"
    ),
]
YEAR = survey_year_filter("intrvw", 2025)
RENT = [{"table": "mtbi", "column": "ucc", "comparison": "in", "value": ["210110"]}]


@pytest.fixture(scope="module")
def processor(tmp_path_factory) -> RequestProcessor:
    service = build_service(MetaSettings(sidecar_dir=ROOT / "sidecars", data_dir=DATA))
    ctx = CaptureContext.from_service(service)
    store = JsonFileStore(tmp_path_factory.mktemp("records"))
    return RequestProcessor(ctx, store, DATA)


def _sql(sql: str) -> list[tuple]:
    con = duckdb.connect()
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _run(processor, *nodes) -> dict:
    answers = [{"asks": f"asks for {n['id']}", "node": n["id"]} for n in nodes]
    text = "In 2025, as released: " + "; ".join(a["asks"] for a in answers) + "."
    output = {
        "nodes": list(nodes),
        "answers": answers,
        "ambiguities": [],
        "unresolved": [],
    }
    record, _ = processor.run(captured(processor.ctx, processor.store, text, output))
    return {n.id: n for n in record.nodes}


def _groups(node) -> dict:
    return {g.group.get("region"): g for g in node.result.groups}


FMLI = duckdb_source(DATA / "intrvw" / "fmli")
MTBI = duckdb_source(DATA / "intrvw" / "mtbi")
POP = "SUM(finlwt21 * mo_scope / 12.0)"
Y = f"""
  SELECT *, CASE quarter_code WHEN '1x' THEN totexpcq WHEN '1' THEN totexppq
            ELSE totexppq + totexpcq END AS x
  FROM {FMLI} WHERE {YEAR}
"""


def test_the_fmli_means_equal_the_skills_worked_sql(processor):
    out = _run(
        processor,
        estimate(id="n1"),
        estimate(id="n2", measure={"table": "fmli", "column": "fincbtxm"}),
    )
    spending = dict(
        _sql(
            f"WITH y AS ({Y}) SELECT region, SUM(x * finlwt21) / {POP} "
            "FROM y WHERE mo_scope IS NOT NULL GROUP BY region"
        )
    )
    income = dict(
        _sql(
            f"WITH y AS ({Y}) SELECT region, SUM(fincbtxm * finlwt21 * mo_scope) / "
            "SUM(finlwt21 * mo_scope) FROM y WHERE mo_scope IS NOT NULL GROUP BY region"
        )
    )
    got1, got2 = _groups(out["n1"]), _groups(out["n2"])
    assert set(got1) == set(spending) == set(got2)
    for region, value in spending.items():
        assert got1[region].value == pytest.approx(value, rel=1e-12)
        assert got2[region].value == pytest.approx(income[region], rel=1e-12)


def test_the_replicate_standard_error_equals_plain_sql(processor):
    out = _run(processor, estimate(id="n1"))
    reps = ", ".join(
        f"SUM(x * wtrep{i:02d}) / SUM(wtrep{i:02d} * mo_scope / 12.0) AS t{i}"
        for i in range(1, 45)
    )
    rows = _sql(
        f"WITH y AS ({Y}) SELECT region, SUM(x * finlwt21) / {POP}, "
        f"{reps} FROM y WHERE mo_scope IS NOT NULL GROUP BY region"
    )
    got = _groups(out["n1"])
    for region, theta, *theta_r in rows:
        se = math.sqrt(sum((t - theta) ** 2 for t in theta_r) / (44 * 0.25))
        assert got[region].standard_error == pytest.approx(se, rel=1e-9)


def test_monthly_rows_mean_equals_plain_sql_for_rent(processor):
    out = _run(
        processor,
        estimate(id="n1", measure={"table": "mtbi", "column": "cost"}, filters=RENT),
    )
    rows = dict(
        _sql(f"""
        WITH f AS (SELECT newid, survey_year, quarter_code, region, finlwt21, mo_scope
                   FROM {FMLI} WHERE {YEAR} AND mo_scope IS NOT NULL),
             m AS (SELECT newid, survey_year, quarter_code, SUM(cost) AS x
                   FROM {MTBI} WHERE {YEAR} AND ucc = '210110' AND ref_yr = '2025'
                     AND coalesce(cost_, '') NOT IN ('A', 'H')
                   GROUP BY ALL)
        SELECT f.region, SUM(coalesce(m.x, 0) * finlwt21) / {POP}
        FROM f LEFT JOIN m USING (newid, survey_year, quarter_code) GROUP BY f.region
    """)
    )
    got = _groups(out["n1"])
    assert set(got) == set(rows)
    for region, value in rows.items():
        assert got[region].value == pytest.approx(value, rel=1e-12)
    assert out["n1"].verdict == "warn"


def test_the_distinct_count_equals_plain_sql(processor):
    count = estimate(
        id="n1",
        statistic="count",
        measure=None,
        stated={
            "weighted": {"value": False, "evidence": "as released"},
            "gap_2025": {"value": "as_released", "evidence": "as released"},
        },
    )
    out = _run(processor, count)
    rows = dict(
        _sql(f"SELECT region, count(DISTINCT cuid) FROM {FMLI} WHERE {YEAR} GROUP BY 1")
    )
    assert {k: g.value for k, g in _groups(out["n1"]).items()} == rows


def test_nodes_on_itbi_and_on_a_detailed_expenditure_table_are_blocked(processor):
    out = _run(
        processor,
        estimate(id="n1", measure={"table": "itbi", "column": "value"}, group_by=[]),
        estimate(
            id="n2", measure={"table": "expn_rnt", "column": "qrt3mcmx"}, group_by=[]
        ),
    )
    assert out["n1"].verdict == out["n2"].verdict == "block"
    assert "unverified_core" in {m.rule for m in out["n1"].messages}
    assert "expn_measure" in {m.rule for m in out["n2"].messages}
