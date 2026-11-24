"""Opening a stored table as a DuckDB source (pipeline step 4).

Only tables in the sidecar set are opened, so a name that is in no sidecar is
never put into SQL. The year set's partitions are applied by `join.build_sql`.
"""

from __future__ import annotations

from pathlib import Path

from ce_measures.errors import DataMissing
from ce_meta.catalog import SURVEY
from ce_meta.sidecars import SidecarSet
from ce_store import duckdb_source


def table_dir(data_dir: Path, table: str) -> Path:
    return Path(data_dir) / SURVEY / table


def open_table(data_dir: Path | None, sidecars: SidecarSet, table: str) -> str:
    """The DuckDB `read_parquet(...)` source of one stored table."""
    found = sidecars.table(table)
    if found is None:
        raise DataMissing(f"No sidecar describes a table named {table!r}.")
    if data_dir is None:
        raise DataMissing("No data folder is configured.")
    folder = table_dir(data_dir, found.name)
    if not any(folder.glob("survey_year=*/quarter_code=*/*.parquet")):
        raise DataMissing(
            f"The table {found.name} has no stored rows; load it with ce-ingest load."
        )
    return duckdb_source(folder)
