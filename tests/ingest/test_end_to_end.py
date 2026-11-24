"""Runs against the real 2025 release in sample_data/. Skipped if it's absent."""

from pathlib import Path

import pyarrow.compute as pc
import pyreadstat
import pytest

from ce_ingest.derive import add_cuid, add_mo_scope, add_partition_columns
from ce_ingest.filename import discover_files
from ce_ingest.normalize import normalize_columns
from ce_ingest.sas_reader import read_sas

LANDING = Path(__file__).parents[3] / "sample_data"
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not (LANDING / "pumd").is_dir(), reason="no sample_data"),
]


def test_discovers_2025_release():
    files = discover_files(LANDING)
    quarterly = [f for f in files if f.kind == "quarterly"]
    assert len(quarterly) == 45
    assert sum(f.survey == "intrvw" for f in quarterly) == 25
    assert sum(f.kind == "multi_quarter" for f in files) == 43


@pytest.mark.parametrize("src", discover_files(LANDING), ids=lambda s: s.label)
def test_read_and_derive(src):
    table, meta, _ = read_sas(src)
    _, header = pyreadstat.read_sas7bdat(str(src.path), metadataonly=True)
    assert table.num_rows == header.number_rows

    table, meta = normalize_columns(table, meta)
    table = add_cuid(table, src)
    table, null_mo_scope = add_mo_scope(table, src)
    table = add_partition_columns(table, src)

    assert table["cuid"].null_count == 0
    assert null_mo_scope == 0
    assert pc.all(pc.equal(pc.utf8_length(table["newid"]), 8)).as_py()
    derived = pc.utf8_slice_codeunits(table["newid"], 0, 7)
    assert pc.all(pc.equal(derived, table["cuid"])).as_py()


def test_reload_fmli_is_idempotent_and_duckdb_reads_it(tmp_path):
    import duckdb

    from ce_ingest.models import LoadResult
    from ce_ingest.registry import REGISTRY_TABLE, record_load, record_source_columns
    from ce_ingest.schema import check_schema, current_schema
    from ce_ingest.writers import ParquetWriter
    from ce_ingest.writers.parquet import duckdb_source

    src = next(f for f in discover_files(LANDING) if f.label == "intrvw25/fmli251x")
    writer = ParquetWriter(tmp_path)
    for _ in range(2):
        table, meta, missing = read_sas(src)
        table, meta = normalize_columns(table, meta)
        table = add_cuid(table, src)
        table, nulls = add_mo_scope(table, src)
        table = add_partition_columns(table, src)
        added, dropped = check_schema(table.schema, current_schema(writer, src), src)
        assert (added, dropped) == ([], [])
        writer.write_partition(src, table)
        record_source_columns(writer, src, meta, missing)
        record_load(
            writer,
            LoadResult(
                source=src,
                status="loaded",
                row_count=table.num_rows,
                checksum="x",
                added_columns=added,
                dropped_columns=dropped,
                missing_counts=missing,
                null_mo_scope_rows=nulls,
                warnings=[],
                error=None,
            ),
        )

    table_dir = writer.table_dir("intrvw", "fmli")
    assert len(list(table_dir.rglob("*.parquet"))) == 1
    con = duckdb.connect()
    n, cuids, qc, year = con.execute(
        f"select count(*), count(distinct cuid), any_value(quarter_code), "
        f"any_value(survey_year) from {duckdb_source(table_dir)}"
    ).fetchone()
    assert (n, qc, year) == (2807, "1x", 2025)
    assert cuids == 2807
    registry = con.execute(
        f"select count(*) from '{writer.meta_path(REGISTRY_TABLE)}'"
    ).fetchone()[0]
    assert registry == 2


def test_load_release_2025(tmp_path):
    import duckdb

    from ce_ingest.config import IngestSettings
    from ce_ingest.pipeline import load_release
    from ce_ingest.writers.parquet import duckdb_source

    settings = IngestSettings(landing_dir=LANDING, out_dir=tmp_path)
    results = load_release(settings, year=2025)
    assert len(results) == 88  # 45 quarterly + 43 EXPN
    assert {r.status for r in results} == {"loaded"}

    expected: dict[tuple[str, str], int] = {}
    for r in results:
        key = (r.source.survey, r.source.table)
        expected[key] = expected.get(key, 0) + r.row_count
    con = duckdb.connect()
    for (survey, table), n in expected.items():
        src = duckdb_source(tmp_path / survey / table)
        assert con.execute(f"select count(*) from {src}").fetchone()[0] == n

    # Every EXPN partition's interviews are in the matching fmli partition.
    fmli = duckdb_source(tmp_path / "intrvw" / "fmli")
    expn = sorted(
        p.name for p in (tmp_path / "intrvw").iterdir() if p.name.startswith("expn_")
    )
    assert len(expn) == 43
    for name in expn:
        orphans = con.execute(
            f"select count(*) from {duckdb_source(tmp_path / 'intrvw' / name)} e "
            f"anti join {fmli} f using (newid, survey_year, quarter_code)"
        ).fetchone()[0]
        assert orphans == 0, name

    # Every stored table is profiled, and no ID column keeps its values.
    prof = f"'{tmp_path / 'meta' / 'column_profile.parquet'}'"
    tables, leaked = con.execute(
        f"""select count(distinct "table"),
                   count(*) filter (where column_name in ('newid', 'cuid')
                                    and top_values is not null)
            from {prof}"""
    ).fetchone()
    assert (tables, leaked) == (53, 0)
