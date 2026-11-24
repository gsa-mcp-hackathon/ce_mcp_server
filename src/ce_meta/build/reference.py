"""The dictionary tables: `ref/variables`, `ref/codes` and `ref/diary_variables`.

Built from the BLS data dictionary's whole history, one row per range of years,
because most variables persist across many releases and a renamed or recoded
one gets a new row. Interview rows only; the Diary names are kept as a bare
list so the service can refuse a Diary variable instead of calling it unknown.
"""

import logging
from collections import defaultdict
from pathlib import Path

import pyarrow as pa

from ce_ingest.models import Survey
from ce_ingest.registry import SOURCE_COLUMNS_TABLE
from ce_ingest.writers.base import Writer
from ce_sidecars.dictionary import DictionaryHistory, covers, read_history

log = logging.getLogger(__name__)

INTERVIEW = "INTERVIEW"
DIARY = "DIARY"
SURVEY: Survey = "intrvw"
EXPN_PREFIX = "expn_"

VARIABLES_TABLE = "variables"
CODES_TABLE = "codes"
DIARY_TABLE = "diary_variables"

VARIABLES_SCHEMA = pa.schema(
    [
        ("variable_name", pa.string()),  # as stored
        ("source_name", pa.string()),  # as BLS spells it
        ("table", pa.string()),
        ("description", pa.string()),
        ("data_type", pa.string()),  # SAS type; null when not in the loaded data
        ("first_year", pa.int32()),
        ("last_year", pa.int32()),  # null = still current
        ("section", pa.string()),
        ("flag_name", pa.string()),
    ]
)
CODES_SCHEMA = pa.schema(
    [
        ("variable_name", pa.string()),
        ("table", pa.string()),
        ("code_value", pa.string()),  # text, exactly as written: "01" is not "1"
        ("code_label", pa.string()),
        ("first_year", pa.int32()),
        ("last_year", pa.int32()),
    ]
)
DIARY_SCHEMA = pa.schema([("variable_name", pa.string())])


def table_name(file: str, stored: set[str]) -> str:
    """The table a dictionary file code describes.

    `FMLI` is the stored `fmli`, `RNT` the stored `expn_rnt`. A file with no
    stored table keeps its lowercased code.
    """
    code = file.strip().lower()
    if code in stored:
        return code
    if EXPN_PREFIX + code in stored:
        return EXPN_PREFIX + code
    return code


def _year_key(year: int | None) -> int:
    return -1 if year is None else year


def build_variables(
    history: DictionaryHistory,
    source_columns: pa.Table | None,
    stored: set[str],
    release: int,
) -> pa.Table:
    """One row per Interview variable, table and range of years."""
    sas_types: dict[tuple[str, str], str] = {}
    if source_columns is not None:
        for r in source_columns.to_pylist():
            if r["survey"] == SURVEY:
                sas_types.setdefault((r["table"], r["name"]), r["sas_type"])

    rows = []
    for row in history.variables:
        if row.survey != INTERVIEW:
            continue
        table = table_name(row.file, stored)
        var = row.variable
        current = covers(row.first_year, row.last_year, release)
        rows.append(
            {
                "variable_name": var.name,
                "source_name": var.source_name,
                "table": table,
                "description": var.description,
                "data_type": sas_types.get((table, var.name)) if current else None,
                "first_year": row.first_year,
                "last_year": row.last_year,
                "section": var.section,
                "flag_name": var.flag,
            }
        )
    rows.sort(
        key=lambda r: (r["table"], r["variable_name"], _year_key(r["first_year"]))
    )
    return pa.Table.from_pylist(rows, schema=VARIABLES_SCHEMA)


def build_diary_variables(history: DictionaryHistory) -> pa.Table:
    """Names that exist in the Diary Survey only."""
    interview = {r.variable.name for r in history.variables if r.survey == INTERVIEW}
    diary = {r.variable.name for r in history.variables if r.survey == DIARY}
    names = sorted(diary - interview)
    return pa.Table.from_pylist(
        [{"variable_name": n} for n in names], schema=DIARY_SCHEMA
    )


def build_codes(history: DictionaryHistory, stored: set[str]) -> pa.Table:
    """One row per Interview code value, variable, table and range of years."""
    seen: set[tuple] = set()
    rows = []
    labels: dict[tuple, list[tuple]] = defaultdict(list)
    for row in history.codes:
        if row.survey != INTERVIEW:
            continue
        table = table_name(row.file, stored)
        key = (
            table,
            row.variable,
            row.code.value,
            row.code.label,
            row.first_year,
            row.last_year,
        )
        if key in seen:
            continue  # an exact duplicate row of the sheet
        seen.add(key)
        labels[(table, row.variable, row.code.value)].append(
            (row.code.label, row.first_year, row.last_year)
        )
        rows.append(
            {
                "variable_name": row.variable,
                "table": table,
                "code_value": row.code.value,
                "code_label": row.code.label,
                "first_year": row.first_year,
                "last_year": row.last_year,
            }
        )

    # A value with two labels in overlapping years is kept, and reported.
    for (table, variable, value), ranges in labels.items():
        for i, (label_a, first_a, last_a) in enumerate(ranges):
            for label_b, first_b, last_b in ranges[i + 1 :]:
                starts_before_end = _year_key(first_b) <= (
                    9999 if last_a is None else last_a
                )
                ends_after_start = (9999 if last_b is None else last_b) >= _year_key(
                    first_a
                )
                if label_a != label_b and starts_before_end and ends_after_start:
                    log.info(
                        "%s.%s code %r has two labels in overlapping years",
                        table,
                        variable,
                        value,
                    )
    rows.sort(
        key=lambda r: (
            r["table"],
            r["variable_name"],
            r["code_value"],
            _year_key(r["first_year"]),
            r["code_label"],
        )
    )
    return pa.Table.from_pylist(rows, schema=CODES_SCHEMA)


def build_reference(writer: Writer, dictionary: Path, release: int) -> dict[str, int]:
    """Build the three dictionary tables and swap each in whole.

    Returns the row count of each table written.
    """
    history = read_history(dictionary)
    stored = {table for survey, table in writer.tables(SURVEY) if survey == SURVEY}
    source_columns = writer.read_rows(SOURCE_COLUMNS_TABLE)
    tables = {
        VARIABLES_TABLE: build_variables(history, source_columns, stored, release),
        CODES_TABLE: build_codes(history, stored),
        DIARY_TABLE: build_diary_variables(history),
    }
    for name, rows in tables.items():
        writer.replace_table("ref", name, rows)
    return {name: rows.num_rows for name, rows in tables.items()}
