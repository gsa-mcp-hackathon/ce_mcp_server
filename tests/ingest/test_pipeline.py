import shutil
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ce_ingest import pipeline
from ce_ingest.config import IngestSettings
from ce_ingest.models import ColumnMeta
from ce_ingest.pipeline import load_file, load_release
from ce_ingest.registry import REGISTRY_TABLE
from ce_ingest.writers import ParquetWriter

from .conftest import DATA


def _meta(table: pa.Table) -> list[ColumnMeta]:
    return [
        ColumnMeta(
            name=n,
            original_name=n,
            sas_type="character" if pa.types.is_string(table[n].type) else "numeric",
            length=8,
            label=None,
            format=None,
        )
        for n in table.column_names
    ]


@pytest.fixture
def fake_sas(monkeypatch):
    """Serve synthetic tables from `read_sas`, keyed by file label."""
    tables: dict[str, pa.Table] = {}

    def read_sas(src, encoding=None):
        t = tables[src.label]
        return t, _meta(t), {n: t[n].null_count for n in t.column_names}

    monkeypatch.setattr(pipeline, "read_sas", read_sas)
    return tables


def _mtbi(cost_type=pa.float64(), extra=False):
    cols = {
        "NEWID": pa.array(["05666064", "05666074"]),
        "COST": pa.array([1.0, None]).cast(cost_type),
    }
    if extra:
        cols["NEWCOL"] = pa.array([1.0, 2.0])
    return pa.table(cols)


def _registry(writer):
    return pq.read_table(writer.meta_path(REGISTRY_TABLE)).to_pylist()


def test_clean_load(fake_sas, make_src, out_dir):
    src = make_src("intrvw25/mtbi251x")
    fake_sas[src.label] = _mtbi()
    w = ParquetWriter(out_dir)
    r = load_file(src.path, w)
    assert (r.status, r.row_count, r.error) == ("loaded", 2, None)
    assert r.missing_counts == {"NEWID": 0, "COST": 1}
    assert len(r.checksum) == 64
    assert (w.partition_dir(src) / "part-0.parquet").exists()
    assert [x["status"] for x in _registry(w)] == ["loaded"]


def test_added_column_warns(fake_sas, make_src, out_dir):
    a, b = make_src("intrvw25/mtbi251x"), make_src("intrvw25/mtbi252")
    fake_sas[a.label], fake_sas[b.label] = _mtbi(), _mtbi(extra=True)
    w = ParquetWriter(out_dir)
    load_file(a.path, w)
    r = load_file(b.path, w)
    assert r.status == "loaded_with_warnings"
    assert r.added_columns == ["newcol"]
    assert r.warnings == ["added columns: newcol"]


def test_type_change_fails_and_keeps_partition(fake_sas, make_src, out_dir):
    a, b = make_src("intrvw25/mtbi251x"), make_src("intrvw25/mtbi252")
    fake_sas[a.label] = fake_sas[b.label] = _mtbi()
    w = ParquetWriter(out_dir)
    load_file(a.path, w)
    load_file(b.path, w)
    before = pq.read_table(w.partition_dir(b) / "part-0.parquet")

    fake_sas[b.label] = _mtbi(cost_type=pa.string())
    r = load_file(b.path, w)
    assert r.status == "failed"
    assert "'cost'" in r.error and "mtbi251x" in r.error
    assert pq.read_table(w.partition_dir(b) / "part-0.parquet").equals(before)
    assert [x["status"] for x in _registry(w)] == ["loaded", "loaded", "failed"]


def test_null_mo_scope_warns(fake_sas, make_src, out_dir):
    src = make_src("intrvw25/fmli251x")
    fake_sas[src.label] = pa.table(
        {
            "NEWID": ["05666064", "05666074"],
            "CUID": [566606.0, 566607.0],
            "QINTRVMO": ["02", None],
        }
    )
    r = load_file(src.path, ParquetWriter(out_dir))
    assert r.status == "loaded_with_warnings"
    assert r.null_mo_scope_rows == 1
    assert r.warnings == ["mo_scope is null on 1 rows"]


def test_unreadable_file_fails(make_src, out_dir):
    src = make_src("diary25/dtbd251")
    src.path.write_bytes(b"not sas")
    w = ParquetWriter(out_dir)
    r = load_file(src.path, w)
    assert r.status == "failed" and "can't read" in r.error
    assert [x["status"] for x in _registry(w)] == ["failed"]


def test_unexpected_error_recorded_then_raised(
    fake_sas, make_src, out_dir, monkeypatch
):
    src = make_src("intrvw25/mtbi251x")
    fake_sas[src.label] = _mtbi()

    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(pipeline, "add_cuid", boom)
    w = ParquetWriter(out_dir)
    with pytest.raises(RuntimeError, match="bug"):
        load_file(src.path, w)
    rows = _registry(w)
    assert rows[0]["status"] == "failed" and "RuntimeError" in rows[0]["error"]


@pytest.fixture
def diary_release(tmp_path) -> Path:
    """dtbd251/252/254 are the real dtbd254 file; dtbd253 is corrupt."""
    folder = tmp_path / "landing" / "pumd" / "diary25"
    folder.mkdir(parents=True)
    for q in ("1", "2", "4"):
        shutil.copyfile(DATA / "dtbd254.sas7bdat", folder / f"dtbd25{q}.sas7bdat")
    (folder / "dtbd253.sas7bdat").write_bytes(b"corrupt")
    return tmp_path / "landing"


def test_load_release_keeps_going(diary_release, out_dir):
    settings = IngestSettings(landing_dir=diary_release, out_dir=out_dir)
    results = load_release(settings)
    assert [(r.source.quarter_code, r.status) for r in results] == [
        ("1", "loaded"),
        ("2", "loaded"),
        ("3", "failed"),
        ("4", "loaded"),
    ]
    prof = pq.read_table(out_dir / "meta" / "column_profile.parquet")
    assert set(prof["table"].to_pylist()) == {"dtbd"}


def test_load_release_year_filter(diary_release, out_dir):
    settings = IngestSettings(landing_dir=diary_release, out_dir=out_dir)
    assert load_release(settings, "diary", 2024) == []
    assert len(load_release(settings, "intrvw", 2025)) == 0


def test_expn_load_writes_five_partitions(fake_sas, make_src, out_dir, tiny_expn):
    src = make_src("intrvw25/expn25/rnt25")
    fake_sas[src.label] = tiny_expn.rename_columns(["NEWID", "QYEAR", "COST"])
    w = ParquetWriter(out_dir)
    r = load_file(src.path, w)
    assert (r.status, r.row_count) == ("loaded", 6)
    parts = sorted(
        p.parent.relative_to(w.table_dir("intrvw", "expn_rnt")).as_posix()
        for p in w.table_dir("intrvw", "expn_rnt").rglob("*.parquet")
    )
    assert parts == [
        "survey_year=2025/quarter_code=1x",
        "survey_year=2025/quarter_code=2",
        "survey_year=2025/quarter_code=3",
        "survey_year=2025/quarter_code=4",
        "survey_year=2026/quarter_code=1",
    ]
    rows = _registry(w)
    assert [(x["quarter_code"], x["row_count"]) for x in rows] == [
        ("1x", 2),
        ("2", 1),
        ("3", 1),
        ("4", 1),
        ("1", 1),
    ]
    assert len({x["checksum"] for x in rows}) == 1


def test_expn_bad_qyear_fails_without_writing(fake_sas, make_src, out_dir, tiny_expn):
    src = make_src("intrvw25/expn25/rnt25")
    bad = tiny_expn.set_column(1, "qyear", pa.array(["20251"] * 5 + ["20271"]))
    fake_sas[src.label] = bad.rename_columns(["NEWID", "QYEAR", "COST"])
    w = ParquetWriter(out_dir)
    r = load_file(src.path, w)
    assert r.status == "failed" and "20271" in r.error
    assert not w.table_dir("intrvw", "expn_rnt").exists()
    assert [(x["status"], x["quarter_code"]) for x in _registry(w)] == [
        ("failed", None)
    ]
