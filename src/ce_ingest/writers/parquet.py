"""Local backend: hive-partitioned Parquet under one output folder.

Layout::

    <out>/<survey>/<table>/survey_year=Y/quarter_code=Q/part-0.parquet
    <out>/meta/<meta_table>.parquet        # the loader's own tables
    <out>/ref/<ref_table>.parquet          # reference tables (`ce-meta build`)
    <out>/_staging/<uuid>/                 # in-flight writes, outside every table

`survey_year` and `quarter_code` live only in the folder names. Readers must
give their types explicitly: inferred from names alone, a table with no `1x`
partition would read `quarter_code` as an integer. The types and the DuckDB
read expression live in `ce_store.partitions`, which the server also imports;
`PARTITION_SCHEMA` is the same types for Arrow.
"""

import logging
import shutil
import uuid
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from ce_ingest.errors import WriteError
from ce_ingest.models import SourceFile, Survey
from ce_ingest.writers.base import Area
from ce_store.partitions import HIVE_TYPES, duckdb_source

log = logging.getLogger(__name__)

_ARROW_TYPES = {"INTEGER": pa.int32(), "VARCHAR": pa.string()}
PARTITION_SCHEMA = pa.schema(
    [pa.field(name, _ARROW_TYPES[sql_type]) for name, sql_type in HIVE_TYPES.items()]
)
PART_FILE = "part-0.parquet"


class ParquetWriter:
    def __init__(self, out_dir: Path) -> None:
        self.out_dir = Path(out_dir)

    # ── layout ───────────────────────────────────────────────────────────────

    def table_dir(self, survey: Survey, table: str) -> Path:
        return self.out_dir / survey / table

    def partition_dir(self, src: SourceFile) -> Path:
        if src.quarter_code is None:
            raise WriteError(f"{src.label}: no quarter code; split the file first")
        return (
            self.table_dir(src.survey, src.table)
            / f"survey_year={src.survey_year}"
            / f"quarter_code={src.quarter_code}"
        )

    def meta_path(self, meta_table: str, area: Area = "meta") -> Path:
        return self.out_dir / area / f"{meta_table}.parquet"

    def _staging_dir(self) -> Path:
        path = self.out_dir / "_staging" / uuid.uuid4().hex
        path.mkdir(parents=True)
        return path

    # ── tables ───────────────────────────────────────────────────────────────

    def read_schema(
        self, survey: Survey, table: str, exclude: SourceFile | None = None
    ) -> pa.Schema | None:
        """Union by name of every partition's schema, plus the partition columns."""
        skip = self.partition_dir(exclude) if exclude is not None else None
        files = [
            f
            for f in sorted(
                self.table_dir(survey, table).glob(
                    "survey_year=*/quarter_code=*/*.parquet"
                )
            )
            if f.parent != skip
        ]
        if not files:
            return None
        schemas = [pq.read_schema(f).remove_metadata() for f in files]
        try:
            unified = pa.unify_schemas(schemas + [PARTITION_SCHEMA])
        except (pa.ArrowInvalid, pa.ArrowTypeError) as exc:
            raise WriteError(
                f"{survey}/{table}: stored partitions disagree on types: {exc}"
            ) from exc
        return unified

    def _partition_files(self, survey: Survey, table: str) -> list[Path]:
        return sorted(
            self.table_dir(survey, table).glob("survey_year=*/quarter_code=*/*.parquet")
        )

    def tables(self, survey: Survey | None = None) -> list[tuple[Survey, str]]:
        found: list[tuple[Survey, str]] = []
        for s in ("diary", "intrvw"):
            if survey is not None and s != survey:
                continue
            root = self.out_dir / s
            if not root.is_dir():
                continue
            for d in sorted(root.iterdir()):
                if d.is_dir() and self._partition_files(s, d.name):  # type: ignore[arg-type]
                    found.append((s, d.name))  # type: ignore[arg-type]
        return found

    def duckdb_from(self, survey: Survey, table: str) -> str | None:
        if not self._partition_files(survey, table):
            return None
        return duckdb_source(self.table_dir(survey, table))

    def write_partition(self, src: SourceFile, table: pa.Table) -> None:
        """Replace one partition. The live partition stays readable on any failure.

        The file is written to a staging folder outside the table first. Then the
        old partition is moved aside, the new one renamed into place, and the
        old one deleted. If the rename fails, the old partition is moved back.
        """
        target = self.partition_dir(src)
        data = table.drop_columns(
            [c for c in PARTITION_SCHEMA.names if c in table.column_names]
        )
        staging = self._staging_dir()
        try:
            pq.write_table(data, staging / PART_FILE)
        except Exception as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise WriteError(f"{src.label}: staging write failed: {exc}") from exc

        target.parent.mkdir(parents=True, exist_ok=True)
        old: Path | None = None
        try:
            if target.exists():
                old = self._staging_dir() / "old"
                target.rename(old)
            staging.rename(target)
        except Exception as exc:
            if old is not None and old.exists() and not target.exists():
                old.rename(target)
            shutil.rmtree(staging, ignore_errors=True)
            raise WriteError(f"{src.label}: swap into {target} failed: {exc}") from exc
        if old is not None:
            shutil.rmtree(old.parent, ignore_errors=True)
        log.info("%s: wrote %d rows to %s", src.label, table.num_rows, target)

    # ── meta tables (small; rewritten in full) ───────────────────────────────

    def read_rows(self, meta_table: str, area: Area = "meta") -> pa.Table | None:
        path = self.meta_path(meta_table, area)
        return pq.read_table(path) if path.exists() else None

    def _write_meta(self, meta_table: str, rows: pa.Table, area: Area = "meta") -> None:
        path = self.meta_path(meta_table, area)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            pq.write_table(rows, tmp)
            tmp.replace(path)
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            raise WriteError(f"{area}/{meta_table}: write failed: {exc}") from exc

    def append_rows(self, meta_table: str, rows: pa.Table, area: Area = "meta") -> None:
        existing = self.read_rows(meta_table, area)
        if existing is not None:
            rows = pa.concat_tables([existing, rows], promote_options="default")
        self._write_meta(meta_table, rows, area)

    def replace_table(self, area: Area, name: str, rows: pa.Table) -> None:
        """Write a whole table: to a temp file first, then swapped in, so a
        failed write leaves the previous file in place."""
        self._write_meta(name, rows, area)

    def replace_rows(
        self,
        meta_table: str,
        rows: pa.Table,
        where: dict[str, object],
        area: Area = "meta",
    ) -> None:
        """Delete the rows matching every `where` equality, then append `rows`."""
        existing = self.read_rows(meta_table, area)
        if existing is not None:
            match = None
            for col, value in where.items():
                eq = pc.equal(existing[col], pa.scalar(value, existing[col].type))
                match = eq if match is None else pc.and_(match, eq)
            keep = existing.filter(pc.invert(pc.fill_null(match, False)))
            rows = pa.concat_tables([keep, rows], promote_options="default")
        self._write_meta(meta_table, rows, area)
