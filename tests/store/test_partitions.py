import subprocess
import sys
from pathlib import Path

from ce_store.partitions import HIVE_TYPES, duckdb_source


def test_duckdb_source_states_the_partition_types():
    src = duckdb_source(Path("/out/diary/fmld"))
    assert "/out/diary/fmld/survey_year=*/quarter_code=*/*.parquet" in src
    assert "hive_partitioning = true" in src and "union_by_name = true" in src
    assert "'survey_year': 'INTEGER'" in src and "'quarter_code': 'VARCHAR'" in src


def test_writer_schema_matches_the_reader_types():
    from ce_ingest.writers.parquet import PARTITION_SCHEMA

    assert PARTITION_SCHEMA.names == list(HIVE_TYPES)


def test_ce_store_needs_no_loader_dependency():
    code = (
        "import sys, ce_store; "
        "heavy = {'pyarrow', 'pandas', 'pyreadstat', 'duckdb', 'ce_ingest'}; "
        "print(sorted(heavy & set(sys.modules)))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]"
