"""Records passed between load steps. All are frozen."""

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ce_store.sets import Survey

FileKind = Literal["quarterly", "multi_quarter"]
Backend = Literal["parquet", "delta"]
LoadStatus = Literal["loaded", "loaded_with_warnings", "failed"]
SasType = Literal["numeric", "character"]

INTERVIEW_TABLES: frozenset[str] = frozenset({"fmli", "memi", "mtbi", "itbi", "itii"})
DIARY_TABLES: frozenset[str] = frozenset({"fmld", "memd", "expd", "dtbd", "dtid"})
TABLES_BY_SURVEY: dict[Survey, frozenset[str]] = {
    "intrvw": INTERVIEW_TABLES,
    "diary": DIARY_TABLES,
}


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class SourceFile(_Frozen):
    """One SAS file, identified from its folder and name."""

    path: Path
    survey: Survey
    table: str  # fmli ... dtid, or expn_<code> for a detailed expenditure file
    survey_year: int  # for multi_quarter, the release year until split
    quarter: int | None  # None for multi_quarter until split_by_qyear
    quarter_code: str | None  # "1x", "1", "2", "3", "4"; None until split
    kind: FileKind = "quarterly"  # multi_quarter = an EXPN file, five quarters

    @property
    def label(self) -> str:
        """Short name used in messages, e.g. `intrvw25/fmli251x`."""
        parts = [self.path.parent.name, self.path.stem]
        if self.kind == "multi_quarter":  # e.g. intrvw25/expn25/rnt25
            parts.insert(0, self.path.parent.parent.name)
        return "/".join(parts)


class ColumnMeta(_Frozen):
    """SAS header metadata for one column."""

    name: str  # normalized
    original_name: str
    sas_type: SasType
    length: int | None
    label: str | None
    format: str | None


class LoadResult(_Frozen):
    """Outcome of loading one file."""

    source: SourceFile
    status: LoadStatus
    row_count: int
    checksum: str  # SHA-256 of the source file
    added_columns: list[str]
    dropped_columns: list[str]
    missing_counts: dict[str, int]
    null_mo_scope_rows: int | None  # fmli / fmld only
    warnings: list[str]
    error: str | None


class RegistryRow(_Frozen):
    """One row of `meta.dataset_registry`: a LoadResult, flattened."""

    source_path: str
    survey: Survey
    table: str
    survey_year: int
    quarter: int | None  # None only for a file that failed before its split
    quarter_code: str | None
    status: LoadStatus
    row_count: int
    checksum: str
    added_columns: list[str]
    dropped_columns: list[str]
    null_mo_scope_rows: int | None
    warnings: list[str]
    error: str | None
    loaded_at: datetime
