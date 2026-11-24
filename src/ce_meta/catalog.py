"""The storage switch of the metadata service: where a stored table is read from.

The service asks a `Catalog` for a table by area and name and never builds a
path itself. `DuckDBCatalog` reads the local Parquet files; a SQL-warehouse
catalog for Databricks is added with the deployment.

Three areas: `raw` (the loaded survey tables), `meta` (the loader's registry,
source columns and column profile) and `ref` (the dictionary tables and the
geographic crosswalk, built by `ce-meta build`).
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Literal, Protocol

import duckdb

from ce_meta.config import MetaSettings
from ce_meta.errors import CatalogMissing
from ce_store import duckdb_source

Area = Literal["raw", "ref", "meta"]

# The command that builds a missing table, by area.
BUILD_COMMANDS: dict[str, str] = {
    "raw": "ce-ingest load",
    "meta": "ce-ingest load",
    "ref": "ce-meta build",
}
SURVEY = "intrvw"  # this service describes the Interview survey only


class Catalog(Protocol):
    def rows(self, sql: str, params: Sequence[object] = ()) -> list[dict]:
        """Run a read-only query and return its rows as dicts."""
        ...

    def source(self, area: Area, name: str) -> str:
        """A FROM expression for a stored table, or `CatalogMissing`."""
        ...

    def has(self, area: Area, name: str) -> bool:
        """Whether a stored table is built."""
        ...


class DuckDBCatalog:
    """Stored tables as local Parquet files, read with DuckDB."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)

    def _file(self, area: Area, name: str) -> Path:
        return self.data_dir / area / f"{name}.parquet"

    def _table_dir(self, name: str) -> Path:
        return self.data_dir / SURVEY / name

    def has(self, area: Area, name: str) -> bool:
        if area == "raw":
            return any(self._table_dir(name).glob("survey_year=*/quarter_code=*/*"))
        return self._file(area, name).exists()

    def source(self, area: Area, name: str) -> str:
        if not self.has(area, name):
            raise CatalogMissing(area, name, BUILD_COMMANDS[area])
        if area == "raw":
            return duckdb_source(self._table_dir(name))
        path = self._file(area, name).as_posix().replace("'", "''")
        return f"read_parquet('{path}')"

    def rows(self, sql: str, params: Sequence[object] = ()) -> list[dict]:
        # A connection per query: tools run in worker threads, and a DuckDB
        # connection is not shared between threads. Nothing is ever written.
        con = duckdb.connect()
        try:
            cursor = con.execute(sql, list(params))
            names = [d[0] for d in cursor.description]
            return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
        finally:
            con.close()


def get_catalog(settings: MetaSettings) -> Catalog:
    if settings.backend == "parquet":
        return DuckDBCatalog(settings.data_dir)
    raise NotImplementedError(
        f"backend {settings.backend!r} is added with the Databricks deployment"
    )
