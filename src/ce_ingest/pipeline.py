"""Load one file, or a whole release, end to end."""

import hashlib
import logging
from pathlib import Path

from ce_ingest.config import IngestSettings
from ce_ingest.derive import (
    add_cuid,
    add_mo_scope,
    add_partition_columns,
    split_by_qyear,
)
from ce_ingest.errors import CeIngestError
from ce_ingest.filename import discover_files, discover_rejects, parse_filename
from ce_ingest.models import LoadResult, LoadStatus, SourceFile, Survey
from ce_ingest.normalize import normalize_columns
from ce_ingest.profile import finish_profile, profile_table
from ce_ingest.registry import (
    earlier_source,
    loaded_release,
    record_load,
    record_source_columns,
)
from ce_ingest.sas_reader import read_sas
from ce_ingest.schema import check_schema, current_schema
from ce_ingest.writers import Writer, get_writer
from ce_store.sets import survey_year_set

log = logging.getLogger(__name__)


def file_checksum(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_file(path: Path, writer: Writer) -> LoadResult:
    """Load one SAS file. Expected failures come back as a `failed` result.

    A name that doesn't parse raises `FilenameError` without a registry row,
    since there is no table or partition to record it against; `load_release`
    only passes files that parsed.
    """
    src = parse_filename(path)
    checksum = ""
    row_count = 0
    missing: dict[str, int] = {}
    added: list[str] = []
    dropped: list[str] = []
    null_mo_scope: int | None = None
    warnings: list[str] = []
    error: str | None = None
    status: LoadStatus = "failed"
    unexpected: BaseException | None = None
    written: list[tuple[SourceFile, int]] = []

    try:
        checksum = file_checksum(path)
        table, meta, missing = read_sas(src)
        row_count = table.num_rows
        table, meta = normalize_columns(table, meta)
        table = add_cuid(table, src)
        table, nulls = add_mo_scope(table, src)
        if src.table in ("fmli", "fmld"):
            null_mo_scope = nulls

        prepared = []
        for part_src, part in split_by_qyear(table, src):
            part = add_partition_columns(part, part_src)
            a, d = check_schema(
                part.schema,
                current_schema(writer, part_src),
                part_src,
                earlier_source=earlier_source(writer, part_src),
            )
            prepared.append((part_src, part))
            added = sorted(set(added) | set(a))
            dropped = sorted(set(dropped) | set(d))
        for part_src, part in prepared:
            writer.write_partition(part_src, part)
            record_source_columns(writer, part_src, meta, missing)
            written.append((part_src, part.num_rows))

        if added:
            warnings.append(f"added columns: {', '.join(added)}")
        if dropped:
            warnings.append(f"dropped columns: {', '.join(dropped)}")
        if null_mo_scope:
            warnings.append(f"mo_scope is null on {null_mo_scope} rows")
        status = "loaded_with_warnings" if warnings else "loaded"
    except CeIngestError as exc:
        error = str(exc)
        log.warning("%s failed: %s", src.label, exc)
    except Exception as exc:  # recorded, then re-raised below
        error = f"unexpected {type(exc).__name__}: {exc}"
        unexpected = exc
    finally:
        result = LoadResult(
            source=src,
            status=status,
            row_count=row_count,
            checksum=checksum,
            added_columns=sorted(added),
            dropped_columns=sorted(dropped),
            missing_counts=missing,
            null_mo_scope_rows=null_mo_scope,
            warnings=warnings,
            error=error,
        )
        # A quarterly file is one row; an EXPN file, one per partition written.
        record_load(writer, result, written if src.kind == "multi_quarter" else None)

    if unexpected is not None:
        raise unexpected
    return result


def _in_release(src: SourceFile, year: int) -> bool:
    if src.kind == "multi_quarter":  # an EXPN file is named for its release
        return src.survey_year == year
    return (src.survey_year, src.quarter_code) in survey_year_set(src.survey, year)


def load_release(
    settings: IngestSettings,
    survey: Survey | None = None,
    year: int | None = None,
    profile: bool = True,
) -> list[LoadResult]:
    """Load every in-scope file, optionally one survey and one release year.

    `year` selects the release: for Interview, year Y is Y's `1x`-`4` plus
    Y+1's `1` (see `ce_store.sets.survey_year_set`).
    """
    writer = get_writer(settings)
    for path, reason in discover_rejects(settings.landing_dir, survey):
        log.warning("skipped %s: %s", path, reason)

    files = discover_files(settings.landing_dir, survey)
    if year is not None:
        files = [f for f in files if _in_release(f, year)]
    results = [load_file(f.path, writer) for f in files]
    if profile:
        changed = sorted(
            {(r.source.survey, r.source.table) for r in results if r.status != "failed"}
        )
        for s, t in changed:
            profile_table(writer, s, t)
        # what needs the dictionary tables, for the surveys just profiled
        for s in sorted({s for s, _ in changed}):
            release = year or loaded_release(writer, s)
            if release is not None:
                finish_profile(writer, s, release)
    return results
