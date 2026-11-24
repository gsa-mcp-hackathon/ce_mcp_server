"""Derived columns: cuid, mo_scope and the partition columns."""

import pyarrow as pa
import pyarrow.compute as pc

from ce_ingest.errors import ColumnCollisionError, MissingKeyError, QuarterError
from ce_ingest.models import SourceFile

PARTITION_COLUMNS: tuple[str, ...] = ("survey_year", "quarter", "quarter_code")
MO_SCOPE_TABLES: frozenset[str] = frozenset({"fmli", "fmld"})


def _to_int64(col: pa.ChunkedArray, *, strict: bool) -> pa.ChunkedArray:
    """Cast a numeric or character column to int64.

    With `strict`, a value that isn't a whole number raises `ArrowInvalid`;
    otherwise it becomes null. Blank strings are always null.
    """
    if pa.types.is_string(col.type) or pa.types.is_large_string(col.type):
        trimmed = pc.utf8_trim_whitespace(col)
        if not strict:
            ok = pc.match_substring_regex(trimmed, r"^[+-]?\d+$")
            trimmed = pc.if_else(ok, trimmed, pa.scalar(None, pa.string()))
        else:
            blank = pc.equal(trimmed, "")
            trimmed = pc.if_else(blank, pa.scalar(None, pa.string()), trimmed)
        return pc.cast(trimmed, pa.int64())
    if pa.types.is_floating(col.type):
        if not strict:
            whole = pc.equal(col, pc.floor(col))
            col = pc.if_else(whole, col, pa.scalar(None, col.type))
        return pc.cast(col, pa.int64())  # safe cast: fractions raise
    return pc.cast(col, pa.int64())


NEWID_WIDTH = 8
CUID_WIDTH = NEWID_WIDTH - 1


def _padded_newid(col: pa.ChunkedArray) -> pa.ChunkedArray:
    """`newid` as an 8-digit string, left-padded with zeros (numeric or character)."""
    if pa.types.is_string(col.type) or pa.types.is_large_string(col.type):
        text = pc.utf8_trim_whitespace(col)
        blank = pc.equal(text, "")
        text = pc.if_else(blank, pa.scalar(None, pa.string()), text)
    else:
        text = pc.cast(_to_int64(col, strict=True), pa.string())
    return pc.utf8_lpad(text, width=NEWID_WIDTH, padding="0")


def add_cuid(table: pa.Table, src: SourceFile) -> pa.Table:
    """Ensure a string `cuid` column: the consumer unit, shared by all its interviews.

    `newid` is always an 8-digit string, left-padded with zeros (it is rewritten
    that way in the table), and `cuid` is `newid` without its last character, the
    interview number: `"05666064"` -> `"0566606"`. A source `cuid` (fmli, fmld)
    is kept, zero-padded to 7 digits, and must equal that on every row.
    """
    names = table.column_names
    if "newid" not in names:
        raise MissingKeyError(f"{src.label}: no newid column")
    try:
        newid = _padded_newid(table["newid"])
    except pa.ArrowInvalid as exc:
        raise MissingKeyError(
            f"{src.label}: newid isn't a whole number: {exc}"
        ) from exc

    if newid.null_count:
        raise MissingKeyError(f"{src.label}: newid is null on {newid.null_count} rows")
    bad = pc.invert(pc.match_substring_regex(newid, rf"^\d{{{NEWID_WIDTH}}}$"))
    n_bad = pc.sum(pc.cast(bad, pa.int64())).as_py() or 0
    if n_bad:
        example = pc.filter(newid, bad)[0].as_py()
        raise MissingKeyError(
            f"{src.label}: newid isn't {NEWID_WIDTH} digits on {n_bad} rows, "
            f"e.g. {example!r}"
        )
    derived = pc.utf8_slice_codeunits(newid, 0, CUID_WIDTH)
    table = table.set_column(names.index("newid"), "newid", newid)

    if "cuid" not in names:
        return table.add_column(names.index("newid") + 1, "cuid", derived)

    try:
        source = pc.cast(_to_int64(table["cuid"], strict=True), pa.string())
    except pa.ArrowInvalid as exc:
        raise MissingKeyError(f"{src.label}: cuid isn't a whole number: {exc}") from exc
    if source.null_count:
        raise MissingKeyError(f"{src.label}: cuid is null on {source.null_count} rows")
    source = pc.utf8_lpad(source, width=CUID_WIDTH, padding="0")
    differs = pc.sum(pc.cast(pc.not_equal(source, derived), pa.int64())).as_py() or 0
    if differs:
        raise MissingKeyError(
            f"{src.label}: cuid differs from newid without its last digit "
            f"on {differs} rows"
        )
    return table.set_column(names.index("cuid"), "cuid", source)


def add_mo_scope(table: pa.Table, src: SourceFile) -> tuple[pa.Table, int]:
    """Add `mo_scope`: how many months of this interview fall in the file's year.

    Only for fmli and fmld; other tables come back unchanged with a count of 0.
    Returns the table and the number of rows where `mo_scope` is null.
    """
    if src.table not in MO_SCOPE_TABLES:
        return table, 0
    if "mo_scope" in table.column_names:
        raise ColumnCollisionError(f"{src.label}: source already has a mo_scope column")

    n = table.num_rows
    if src.table == "fmli" and src.quarter_code in ("1x", "1"):
        if "qintrvmo" in table.column_names:
            month = _to_int64(table["qintrvmo"], strict=False)
        else:
            month = pa.chunked_array([pa.nulls(n, pa.int64())])
        valid = pc.and_(pc.greater_equal(month, 1), pc.less_equal(month, 3))
        if src.quarter_code == "1x":
            scope = pc.subtract(month, 1)
        else:
            scope = pc.subtract(4, month)
        scope = pc.if_else(valid, scope, pa.scalar(None, pa.int64()))
        mo_scope = pc.cast(scope, pa.int32())
    else:
        mo_scope = pa.chunked_array([pa.array([3] * n, pa.int32())])

    return table.append_column("mo_scope", mo_scope), mo_scope.null_count


def add_partition_columns(table: pa.Table, src: SourceFile) -> pa.Table:
    """Append `survey_year`, `quarter` and `quarter_code`, constant on every row."""
    clash = [c for c in PARTITION_COLUMNS if c in table.column_names]
    if clash:
        raise ColumnCollisionError(
            f"{src.label}: source already has partition column(s) {', '.join(clash)}"
        )
    n = table.num_rows
    return (
        table.append_column(
            pa.field("survey_year", pa.int32()),
            pa.array([src.survey_year] * n, pa.int32()),
        )
        .append_column(
            pa.field("quarter", pa.int32()), pa.array([src.quarter] * n, pa.int32())
        )
        .append_column(
            pa.field("quarter_code", pa.string()),
            pa.array([src.quarter_code] * n, pa.string()),
        )
    )


def _qyear_codes(release: int) -> dict[str, tuple[int, int, str]]:
    """Valid `qyear` values of one release -> (survey_year, quarter, quarter_code)."""
    codes = {f"{release}1": (release, 1, "1x")}
    codes |= {f"{release}{q}": (release, q, str(q)) for q in (2, 3, 4)}
    codes[f"{release + 1}1"] = (release + 1, 1, "1")
    return codes


def split_by_qyear(
    table: pa.Table, src: SourceFile
) -> list[tuple[SourceFile, pa.Table]]:
    """Split an EXPN table into one slice per interview quarter.

    `qyear` is the interview year and quarter, e.g. `"20252"`. With the release
    year R from the folder: R quarter 1 -> `1x`, R quarters 2-4 -> `2`-`4`,
    R+1 quarter 1 -> `1`. The `qyear` column is kept. A quarterly table comes
    back unchanged as one slice.
    """
    if src.kind != "multi_quarter":
        return [(src, table)]
    if "qyear" not in table.column_names:
        raise QuarterError(f"{src.label}: no qyear column")

    col = table["qyear"]
    if not (pa.types.is_string(col.type) or pa.types.is_large_string(col.type)):
        col = pc.cast(_to_int64(col, strict=False), pa.string())
    col = pc.utf8_trim_whitespace(col)
    if col.null_count:
        raise QuarterError(f"{src.label}: qyear is null on {col.null_count} rows")

    codes = _qyear_codes(src.survey_year)
    present = set(pc.unique(col).to_pylist())
    unknown = sorted(present - codes.keys())
    if unknown:
        raise QuarterError(
            f"{src.label}: qyear {', '.join(unknown)} is outside release "
            f"{src.survey_year} ({', '.join(codes)})"
        )

    slices: list[tuple[SourceFile, pa.Table]] = []
    for value, (year, quarter, code) in codes.items():
        if value not in present:
            continue
        part_src = src.model_copy(
            update={"survey_year": year, "quarter": quarter, "quarter_code": code}
        )
        slices.append((part_src, table.filter(pc.equal(col, value))))
    return slices
