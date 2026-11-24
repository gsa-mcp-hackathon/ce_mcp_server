"""The storage boundary. Only writers know which backend is in use."""

from typing import Literal, Protocol

import pyarrow as pa

from ce_ingest.models import SourceFile, Survey

# Where a small table lives: `meta` holds the loader's own tables (registry,
# source columns, profile); `ref` holds reference tables built from the BLS
# dictionary (`ce-meta build`).
Area = Literal["meta", "ref"]


class Writer(Protocol):
    def read_schema(
        self, survey: Survey, table: str, exclude: SourceFile | None = None
    ) -> pa.Schema | None:
        """Combined schema of the live table's partitions, without `exclude`."""
        ...

    def write_partition(self, src: SourceFile, table: pa.Table) -> None: ...

    def tables(self, survey: Survey | None = None) -> list[tuple[Survey, str]]:
        """Stored tables that have at least one partition."""
        ...

    def duckdb_from(self, survey: Survey, table: str) -> str | None:
        """A DuckDB FROM expression for a stored table, or None if absent."""
        ...

    def read_rows(self, meta_table: str, area: Area = "meta") -> pa.Table | None:
        """A whole meta or ref table, or None if it doesn't exist yet."""
        ...

    def append_rows(
        self, meta_table: str, rows: pa.Table, area: Area = "meta"
    ) -> None: ...

    def replace_rows(
        self,
        meta_table: str,
        rows: pa.Table,
        where: dict[str, object],
        area: Area = "meta",
    ) -> None: ...

    def replace_table(self, area: Area, name: str, rows: pa.Table) -> None:
        """Write a whole table, leaving the previous one in place on failure."""
        ...
