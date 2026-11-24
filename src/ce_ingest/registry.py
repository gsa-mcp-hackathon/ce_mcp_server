"""Load history (`dataset_registry`) and SAS column metadata (`source_columns`)."""

from datetime import UTC, datetime

import pyarrow as pa

from ce_ingest.models import ColumnMeta, LoadResult, RegistryRow, SourceFile
from ce_ingest.writers.base import Writer

REGISTRY_TABLE = "dataset_registry"
SOURCE_COLUMNS_TABLE = "source_columns"

REGISTRY_SCHEMA = pa.schema(
    [
        ("source_path", pa.string()),
        ("survey", pa.string()),
        ("table", pa.string()),
        ("survey_year", pa.int32()),
        ("quarter", pa.int32()),
        ("quarter_code", pa.string()),
        ("status", pa.string()),
        ("row_count", pa.int64()),
        ("checksum", pa.string()),
        ("added_columns", pa.list_(pa.string())),
        ("dropped_columns", pa.list_(pa.string())),
        ("null_mo_scope_rows", pa.int64()),
        ("warnings", pa.list_(pa.string())),
        ("error", pa.string()),
        ("loaded_at", pa.timestamp("us", tz="UTC")),
    ]
)

SOURCE_COLUMNS_SCHEMA = pa.schema(
    [
        ("survey", pa.string()),
        ("table", pa.string()),
        ("survey_year", pa.int32()),
        ("quarter_code", pa.string()),
        ("position", pa.int32()),
        ("name", pa.string()),
        ("original_name", pa.string()),
        ("sas_type", pa.string()),
        ("length", pa.int32()),
        ("label", pa.string()),
        ("format", pa.string()),
        ("null_count", pa.int64()),
    ]
)


def registry_row(result: LoadResult, loaded_at: datetime | None = None) -> RegistryRow:
    src = result.source
    return RegistryRow(
        source_path=str(src.path),
        survey=src.survey,
        table=src.table,
        survey_year=src.survey_year,
        quarter=src.quarter,
        quarter_code=src.quarter_code,
        status=result.status,
        row_count=result.row_count,
        checksum=result.checksum,
        added_columns=result.added_columns,
        dropped_columns=result.dropped_columns,
        null_mo_scope_rows=result.null_mo_scope_rows,
        warnings=result.warnings,
        error=result.error,
        loaded_at=loaded_at or datetime.now(UTC),
    )


def record_load(
    writer: Writer,
    result: LoadResult,
    partitions: list[tuple[SourceFile, int]] | None = None,
) -> None:
    """Append this load attempt to the registry, failed or not.

    One row per partition written, each with its own row count and the file's
    checksum; with no `partitions` (a quarterly file, or a failure before any
    partition was known), one row for the file.
    """
    loaded_at = datetime.now(UTC)
    base = registry_row(result, loaded_at)
    if not partitions:
        rows = [base.model_dump()]
    else:
        rows = [
            base.model_copy(
                update={
                    "survey_year": part.survey_year,
                    "quarter": part.quarter,
                    "quarter_code": part.quarter_code,
                    "row_count": n,
                }
            ).model_dump()
            for part, n in partitions
        ]
    writer.append_rows(
        REGISTRY_TABLE, pa.Table.from_pylist(rows, schema=REGISTRY_SCHEMA)
    )


def record_source_columns(
    writer: Writer,
    src: SourceFile,
    meta: list[ColumnMeta],
    missing_counts: dict[str, int] | None = None,
) -> None:
    """Replace this partition's column metadata, so reloads stay idempotent.

    `missing_counts` is `read_sas`'s nulls per column, keyed by original name;
    it fills `null_count` (null when not given).
    """
    missing_counts = missing_counts or {}
    rows = [
        {
            "survey": src.survey,
            "table": src.table,
            "survey_year": src.survey_year,
            "quarter_code": src.quarter_code,
            "position": i,
            **m.model_dump(),
            "null_count": missing_counts.get(m.original_name),
        }
        for i, m in enumerate(meta)
    ]
    writer.replace_rows(
        SOURCE_COLUMNS_TABLE,
        pa.Table.from_pylist(rows, schema=SOURCE_COLUMNS_SCHEMA),
        where={
            "survey": src.survey,
            "table": src.table,
            "survey_year": src.survey_year,
            "quarter_code": src.quarter_code,
        },
    )


def earlier_source(writer: Writer, src: SourceFile) -> str | None:
    """The most recent loaded file of this table in another partition, if any.

    Names the file that set the stored types, for `TypeChangeError` messages.
    """
    rows = writer.read_rows(REGISTRY_TABLE)
    if rows is None:
        return None
    best: tuple[datetime, str] | None = None
    for r in rows.to_pylist():
        if r["survey"] != src.survey or r["table"] != src.table:
            continue
        if r["status"] == "failed":
            continue
        if (r["survey_year"], r["quarter_code"]) == (src.survey_year, src.quarter_code):
            continue
        if best is None or r["loaded_at"] > best[0]:
            best = (r["loaded_at"], r["source_path"])
    return best[1] if best else None


def loaded_release(writer: Writer, survey: str) -> int | None:
    """The latest release year loaded for a survey, from the registry.

    An Interview file with quarter code `1` belongs to the release before its
    survey year, so it does not count toward its own year.
    """
    rows = writer.read_rows(REGISTRY_TABLE)
    if rows is None:
        return None
    years = [
        r["survey_year"]
        for r in rows.to_pylist()
        if r["survey"] == survey
        and r["status"] != "failed"
        and r["survey_year"] is not None
        and not (survey == "intrvw" and r["quarter_code"] == "1")
    ]
    return max(years, default=None)
