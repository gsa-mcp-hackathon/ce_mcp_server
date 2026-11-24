"""Fixtures for request processing (Plan 5).

- `tiny`: a small sidecar set (fmli, mtbi, memi, itii) and small stored tables
  over the five files of survey year 2025, plus the `1` file of 2025 that
  belongs to survey year 2024 and must never be read for 2025.
- `real_ctx`: the real sidecar set with no data, for verdicts.
- Helpers that turn a request record's nodes into a record pair, through
  Plan 4's derivation, so the pairs are what capture really writes.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pytest

from ce_capture.context import CaptureContext
from ce_capture.derive import derive
from ce_capture.pipeline import CapturePipeline, Submission
from ce_capture.storage import JsonFileStore
from ce_mcp_server.service import build_service
from ce_measures.models import Node
from ce_measures.process import RequestProcessor
from ce_measures.records import RecordPair, nodes
from ce_meta.config import MetaSettings
from tests.conftest import (
    FMLI,
    MTBI,
    _cp,
    column,
    make_service,
    small_set_docs,
    table_doc,
    write_set,
)

ROOT = Path(__file__).parents[2]
SAMPLES = ROOT.parent
MEMI = "ce-interview-memi"
ITII = "ce-interview-itii"
REPLICATES = 44

# ── the stored rows ──────────────────────────────────────────────────────────
# fmli: (survey_year, quarter_code, newid, cuid, region, state, mo_scope,
#        finlwt21, totexppq, totexpcq, fincbtxm, qintrvyr, qintrvmo, cutenure)
FMLI_ROWS = [
    (
        2025,
        "1x",
        "11",
        "A",
        "1",
        "01",
        2,
        100.0,
        300.0,
        600.0,
        50000.0,
        "2025",
        "03",
        "1",
    ),
    (
        2025,
        "1x",
        "12",
        "B",
        "2",
        "06",
        1,
        200.0,
        0.0,
        300.0,
        40000.0,
        "2025",
        "02",
        "4",
    ),
    (
        2025,
        "2",
        "21",
        "A",
        "1",
        "01",
        3,
        100.0,
        600.0,
        900.0,
        50000.0,
        "2025",
        "06",
        "1",
    ),
    (
        2025,
        "2",
        "22",
        "B",
        "2",
        "06",
        3,
        200.0,
        300.0,
        300.0,
        40000.0,
        "2025",
        "05",
        "4",
    ),
    (2025, "2", "23", "C", "C", "", 3, 150.0, 100.0, 200.0, 30000.0, "2025", "04", "4"),
    (
        2025,
        "3",
        "31",
        "A",
        "1",
        "01",
        3,
        100.0,
        900.0,
        300.0,
        52000.0,
        "2025",
        "08",
        "1",
    ),
    (
        2025,
        "3",
        "32",
        "D",
        "3",
        "06",
        None,
        120.0,
        500.0,
        500.0,
        20000.0,
        "2025",
        "13",
        "1",
    ),
    (
        2025,
        "4",
        "41",
        "B",
        "2",
        "06",
        3,
        200.0,
        300.0,
        600.0,
        41000.0,
        "2025",
        "12",
        "4",
    ),
    (2026, "1", "51", "C", "C", "", 2, 150.0, 400.0, 100.0, 31000.0, "2026", "02", "4"),
    # survey year 2024's set: never part of 2025
    (
        2025,
        "1",
        "11",
        "A",
        "1",
        "01",
        1,
        100.0,
        300.0,
        600.0,
        50000.0,
        "2025",
        "03",
        "1",
    ),
    (
        2025,
        "1",
        "99",
        "E",
        "4",
        "06",
        3,
        900.0,
        9000.0,
        9000.0,
        99000.0,
        "2025",
        "01",
        "4",
    ),
]
# mtbi: (survey_year, quarter_code, newid, ucc, ref_yr, ref_mo, cost, cost_)
MTBI_ROWS = [
    (2025, "2", "21", "210110", "2025", "04", 1000.0, "D"),
    (2025, "2", "21", "210110", "2025", "05", 1000.0, "D"),
    (2025, "2", "21", "790210", "2025", "04", 300.0, "D"),
    (2025, "2", "22", "210110", "2025", "04", 800.0, "D"),
    (2025, "2", "22", "210110", "2025", "05", 500.0, "H"),  # a parent record
    (2025, "1x", "11", "210110", "2024", "12", 900.0, "D"),  # the year before
    (2025, "1x", "11", "210110", "2025", "01", 900.0, "D"),
    (2025, "4", "41", "790210", "2025", "10", -50.0, "D"),  # a refund
    (2025, "3", "31", "210110", "2025", "08", 700.0, "D"),
    (2025, "1", "99", "210110", "2025", "01", 5000.0, "D"),  # 2024's set
]
# memi: (survey_year, quarter_code, newid, cuid, membno, age, sex)
MEMI_ROWS = [
    (2025, "1x", "11", "A", 1.0, 70.0, "1"),
    (2025, "1x", "12", "B", 1.0, 40.0, "2"),
    (2025, "2", "21", "A", 1.0, 70.0, "1"),
    (2025, "2", "21", "A", 2.0, 68.0, "2"),
    (2025, "2", "22", "B", 1.0, 40.0, "2"),
    (2025, "2", "23", "C", 1.0, 30.0, "1"),
    (2025, "3", "31", "A", 1.0, 70.0, "1"),
    (2025, "3", "32", "D", 1.0, 66.0, "2"),
    (2025, "4", "41", "B", 1.0, 41.0, "2"),
    (2026, "1", "51", "C", 1.0, 30.0, "1"),
]
# itii: (survey_year, quarter_code, newid, ucc, refyr, refmo, impnum, value)
ITII_ROWS = [
    (2025, "2", "21", "900000", "2025", "04", str(i), 1000.0 + 10 * i)
    for i in range(1, 6)
] + [(2025, "2", "22", "900000", "2025", "04", str(i), 500.0) for i in range(1, 6)]


def replicate(w: float, i: int, newid: str) -> float:
    """A replicate weight that differs from the full weight by replicate and by
    interview, so that a ratio's replicates differ too."""
    return w * (1 + (((i + int(newid)) % 4) - 1.5) * 0.1)


# ── the sidecars ─────────────────────────────────────────────────────────────


def tiny_docs() -> dict[str, dict]:
    docs = small_set_docs()
    fmli = docs[f"{FMLI}.odcs.yaml"]["schema"][0]["properties"]
    fmli += [
        column(
            "mo_scope",
            "Months in scope",
            ce_role="measure",
            ce_scale="ratio",
            ce_unit="months",
            ce_level="consumer_unit",
        ),
        column("qintrvmo", "Interview month", ce_role="period", ce_scale="ordinal"),
        column(
            "fam_size",
            "Consumer unit size",
            ce_role="dimension",
            ce_scale="ratio",
            ce_unit="count",
            ce_level="consumer_unit",
        ),
        column(
            "ordinal_thing",
            "An ordinal",
            ce_role="measure",
            ce_scale="ordinal",
            ce_unit="count",
            ce_level="consumer_unit",
        ),
    ]
    mtbi = docs[f"{MTBI}.odcs.yaml"]["schema"][0]["properties"]
    mtbi.append(
        column(
            "cost_",
            "Flag for cost",
            ce_role="flag",
            ce_flagFor="cost",
            ce_flagMeanings={"D": "valid", "A": "skip_pattern", "H": "parent_record"},
        )
    )
    memi_columns = [
        column(
            "newid", "Interview identifier", ce_role="identifier", ce_scale="nominal"
        ),
        column(
            "cuid", "Consumer unit identifier", ce_role="identifier", ce_scale="nominal"
        ),
        column("membno", "Member number", ce_role="identifier", ce_scale="nominal"),
        column(
            "age",
            "Age",
            ce_role="measure",
            ce_scale="ratio",
            ce_unit="years",
            ce_level="member",
        ),
        column(
            "sex",
            "Sex",
            ce_role="dimension",
            ce_scale="nominal",
            enum=[{"value": "1", "label": "Male"}, {"value": "2", "label": "Female"}],
        ),
    ]
    itii_columns = [
        column(
            "newid", "Interview identifier", ce_role="identifier", ce_scale="nominal"
        ),
        column("ucc", "Income item code", ce_role="item_code", ce_scale="nominal"),
        column("refyr", "Reference year", ce_role="period", ce_scale="interval"),
        column("refmo", "Reference month", ce_role="period", ce_scale="ordinal"),
        column("impnum", "Imputation number", ce_role="identifier", ce_scale="nominal"),
        column(
            "value",
            "Value of item",
            ce_role="measure",
            ce_scale="ratio",
            ce_unit="usd_monthly",
            ce_level="consumer_unit",
            ce_additiveOver=["consumer_units", "months_in_scope"],
            ce_imputed={"multiple": True, "iterations": 5},
        ),
    ]
    docs[f"{MEMI}.odcs.yaml"] = table_doc(
        "memi",
        "member_interview",
        ["newid", "membno"],
        "One row per member.",
        "Members of each consumer unit, per interview.",
        memi_columns,
    )
    docs[f"{ITII}.odcs.yaml"] = table_doc(
        "itii",
        "imputed_income_item_month",
        ["newid", "ucc", "refyr", "refmo", "impnum"],
        "One row per income item, month and imputed version.",
        "Imputed income items by month.",
        itii_columns,
    )
    master = docs["ce-interview-master.odcs.yaml"]
    props = {cp["property"]: cp["value"] for cp in master["customProperties"]}
    props["ce:tables"] += [
        {
            "sidecar": MEMI,
            "version": "0.1.0",
            "rowRepresents": "member_interview",
            "carriesWeight": False,
        },
        {
            "sidecar": ITII,
            "version": "0.1.0",
            "rowRepresents": "imputed_income_item_month",
            "carriesWeight": False,
        },
    ]
    props["ce:relationships"] += [
        {
            "parent": FMLI,
            "child": MEMI,
            "keys": {"parent": ["newid"], "child": ["newid"]},
            "cardinality": "one_to_many",
            "childRowsOptional": False,
            "weightFrom": "parent",
        },
        {
            "parent": FMLI,
            "child": ITII,
            "keys": {"parent": ["newid"], "child": ["newid"]},
            "cardinality": "one_to_many",
            "childRowsOptional": True,
            "weightFrom": "parent",
        },
    ]
    props["ce:variance"]["byRelease"].insert(
        0, {"releases": "<=2024", "method": "brr", "R": 44, "k": 0}
    )
    master["customProperties"] = _cp(props)
    return docs


# ── the stored tables ────────────────────────────────────────────────────────


def _write(con, data_dir: Path, table: str, columns: str, rows: list[tuple]) -> None:
    by_part: dict[tuple, list] = {}
    for row in rows:
        by_part.setdefault((row[0], row[1]), []).append(row[2:])
    for (year, code), part in by_part.items():
        folder = (
            data_dir / "intrvw" / table / f"survey_year={year}" / f"quarter_code={code}"
        )
        folder.mkdir(parents=True)
        con.execute(f"create or replace table part ({columns})")
        n = columns.count(",") + 1
        con.executemany(f"insert into part values ({', '.join(['?'] * n)})", part)
        con.execute(f"copy part to '{folder / 'part-0.parquet'}' (format parquet)")


def write_tiny_data(data_dir: Path) -> Path:
    con = duckdb.connect()
    reps = ", ".join(f"wtrep{i:02d} double" for i in range(1, REPLICATES + 1))
    fmli = [
        (
            *r[:8],
            *[replicate(r[7], i, r[2]) for i in range(1, REPLICATES + 1)],
            *r[8:],
            *[r[10] + 1000 * (v - 3) for v in range(1, 6)],
        )
        for r in FMLI_ROWS
    ]
    versions = ", ".join(f"fincbtx{v} double" for v in range(1, 6))
    _write(
        con,
        data_dir,
        "fmli",
        "newid varchar, cuid varchar, region varchar, state varchar, mo_scope integer, "
        f"finlwt21 double, {reps}, totexppq double, totexpcq double, fincbtxm double, "
        f"qintrvyr varchar, qintrvmo varchar, cutenure varchar, {versions}",
        fmli,
    )
    _write(
        con,
        data_dir,
        "mtbi",
        "newid varchar, ucc varchar, ref_yr varchar, ref_mo varchar, cost double, "
        "cost_ varchar",
        MTBI_ROWS,
    )
    _write(
        con,
        data_dir,
        "memi",
        "newid varchar, cuid varchar, membno double, age double, sex varchar",
        MEMI_ROWS,
    )
    _write(
        con,
        data_dir,
        "itii",
        "newid varchar, ucc varchar, refyr varchar, refmo varchar, impnum varchar, "
        "value double",
        ITII_ROWS,
    )
    meta = data_dir / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    con.execute(
        'create table registry (survey varchar, "table" varchar, survey_year integer, '
        "quarter_code varchar, status varchar, row_count bigint, loaded_at timestamptz)"
    )
    parts = sorted({(r[0], r[1]) for r in FMLI_ROWS})
    con.executemany(
        "insert into registry values ('intrvw', 'fmli', ?, ?, 'loaded', 1, "
        "'2026-11-17 10:00:00+00')",
        parts,
    )
    con.execute(
        f"copy registry to '{meta / 'dataset_registry.parquet'}' (format parquet)"
    )
    con.close()
    return data_dir


@dataclass
class Tiny:
    ctx: CaptureContext
    store: JsonFileStore
    data_dir: Path
    processor: RequestProcessor

    @property
    def sidecars(self):
        return self.ctx.sidecars


@pytest.fixture
def tiny(tmp_path) -> Tiny:
    sidecar_dir = write_set(tmp_path / "sidecars", tiny_docs())
    data_dir = write_tiny_data(tmp_path / "data")
    service = make_service(sidecar_dir, data_dir)
    assert service.report.ok, list(service.report.lines())
    ctx = CaptureContext.from_service(service)
    store = JsonFileStore(tmp_path / "records")
    return Tiny(ctx, store, data_dir, RequestProcessor(ctx, store, data_dir))


@pytest.fixture(scope="session")
def real_ctx() -> CaptureContext:
    """The real sidecar set, with no data loaded (as in CI)."""
    settings = MetaSettings(sidecar_dir=ROOT / "sidecars", data_dir=ROOT / "no-data")
    return CaptureContext.from_service(build_service(settings))


# ── record pairs ─────────────────────────────────────────────────────────────


def estimate(**changes) -> dict:
    """An estimate node: weighted mean of totexpcq by region, 2025 as released."""
    base = {
        "id": "n1",
        "kind": "estimate",
        "statistic": "mean",
        "measure": {"table": "fmli", "column": "totexpcq"},
        "group_by": [{"table": "fmli", "column": "region"}],
        "filters": [],
        "period": {"text": "in 2025", "kind": "year", "values": [2025]},
        "stated": {"gap_2025": {"value": "as_released", "evidence": "as released"}},
    }
    base.update(copy.deepcopy(changes))
    return base


def request_record(*node_list: dict, answers: list | None = None, flags=None) -> dict:
    node_list = node_list or (estimate(),)
    return {
        "request_id": "req-test",
        "captured_at": "2026-11-20T09:00:00+00:00",
        "source": {"user_text": "test", "session_id": "s1", "turn": 1},
        "capture": {"status": "captured", "reason": None},
        "sidecars": {},
        "nodes": list(node_list),
        "answers": answers
        if answers is not None
        else [{"asks": f"asks {n['id']}", "node": n["id"]} for n in node_list],
        "check": {"passed": True, "errors": [], "flags": list(flags or [])},
    }


def pair_of(ctx: CaptureContext, *node_list: dict, flags=None, **kw) -> RecordPair:
    request = request_record(*node_list, flags=flags, **kw)
    resolved = derive(request, ctx, "res-test", "2026-11-20T09:00:01+00:00")
    return RecordPair(request, resolved)


def node_of(ctx: CaptureContext, asked: dict | None = None, **changes) -> Node:
    asked = asked or estimate(**changes)
    return nodes(pair_of(ctx, asked))[0]


def store_pair(store: JsonFileStore, pair: RecordPair) -> str:
    store.write("request", pair.request)
    store.write("resolved", pair.resolved)
    return pair.request["request_id"]


def sample_output() -> dict | None:
    path = SAMPLES / "sample-request-record.json"
    if not path.exists():
        return None
    sample = json.loads(path.read_text())
    return {
        "text": sample["source"]["user_text"],
        "output": {
            k: sample[k] for k in ("nodes", "answers", "ambiguities", "unresolved")
        },
    }


def captured(ctx: CaptureContext, store: JsonFileStore, text: str, output: dict) -> str:
    """Submit through Plan 4's pipeline; the request ID of the captured record."""
    reply = CapturePipeline(ctx, store).submit(Submission(text, "s1", output))
    assert reply["status"] == "captured", reply
    return reply["request_id"]
