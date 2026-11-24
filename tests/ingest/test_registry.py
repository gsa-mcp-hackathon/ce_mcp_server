import pyarrow.parquet as pq

from ce_ingest.models import ColumnMeta, LoadResult
from ce_ingest.registry import (
    REGISTRY_TABLE,
    SOURCE_COLUMNS_TABLE,
    loaded_release,
    record_load,
    record_source_columns,
)
from ce_ingest.writers import ParquetWriter


def _result(src, status="loaded", error=None):
    return LoadResult(
        source=src,
        status=status,
        row_count=0 if error else 4,
        checksum="ab" * 32,
        added_columns=["new_col"] if status == "loaded_with_warnings" else [],
        dropped_columns=[],
        missing_counts={},
        null_mo_scope_rows=None,
        warnings=[],
        error=error,
    )


def test_record_load_success_and_failure(out_dir, make_src):
    w = ParquetWriter(out_dir)
    src = make_src("intrvw25/fmli252")
    record_load(w, _result(src, "loaded_with_warnings"))
    record_load(w, _result(src, "failed", error="intrvw25/fmli252: boom"))
    rows = pq.read_table(w.meta_path(REGISTRY_TABLE)).to_pylist()
    assert [r["status"] for r in rows] == ["loaded_with_warnings", "failed"]
    assert rows[0]["added_columns"] == ["new_col"]
    assert rows[1]["error"] == "intrvw25/fmli252: boom"
    assert rows[0]["quarter_code"] == "2" and rows[0]["table"] == "fmli"
    assert rows[0]["loaded_at"] is not None


def _meta(*names):
    return [
        ColumnMeta(
            name=n.lower(),
            original_name=n,
            sas_type="numeric",
            length=8,
            label=None,
            format="12.",
        )
        for n in names
    ]


def test_record_source_columns_is_idempotent(out_dir, make_src):
    w = ParquetWriter(out_dir)
    a = make_src("intrvw25/fmli252")
    b = make_src("intrvw25/fmli253")
    record_source_columns(w, a, _meta("NEWID", "FINLWT21"))
    record_source_columns(w, b, _meta("NEWID"))
    record_source_columns(w, a, _meta("NEWID", "FINLWT21", "CUID"))  # reload a
    rows = pq.read_table(w.meta_path(SOURCE_COLUMNS_TABLE)).to_pylist()
    by_qc = {}
    for r in rows:
        by_qc.setdefault(r["quarter_code"], []).append(
            (r["position"], r["original_name"])
        )
    assert sorted(by_qc["2"]) == [(0, "NEWID"), (1, "FINLWT21"), (2, "CUID")]
    assert by_qc["3"] == [(0, "NEWID")]


def test_record_source_columns_null_count(out_dir, make_src):
    w = ParquetWriter(out_dir)
    src = make_src("intrvw25/fmli252")
    record_source_columns(w, src, _meta("NEWID", "FINLWT21"), {"FINLWT21": 7})
    rows = pq.read_table(w.meta_path(SOURCE_COLUMNS_TABLE)).to_pylist()
    assert {r["name"]: r["null_count"] for r in rows} == {"newid": None, "finlwt21": 7}


def test_loaded_release_ignores_the_next_years_first_quarter(out_dir, make_src):
    w = ParquetWriter(out_dir)
    assert loaded_release(w, "intrvw") is None
    for rel in ("intrvw25/fmli254", "intrvw25/fmli261"):
        record_load(w, _result(make_src(rel)))
    record_load(w, _result(make_src("diary25/fmld254")))
    # the 2026 `1` file belongs to the 2025 release
    assert loaded_release(w, "intrvw") == 2025
    assert loaded_release(w, "diary") == 2025
