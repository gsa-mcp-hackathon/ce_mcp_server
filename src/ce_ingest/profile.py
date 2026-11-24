"""Column profiles of stored tables (`meta/column_profile`).

One row per column of a stored table, across all its partitions: null count
and share, min, max, distinct count, and the 20 most common values. Wide
tables are unpivoted in memory to rank values in one pass; item tables are
ranked column by column. Nothing long-format is stored.

Once the dictionary tables exist (`ce-meta build`), `finish_profile` adds what
needs them: whether each column is documented, and `meta/unmatched_codes`, the
values in the data that are not in the column's code list.
"""

import logging
import re
from datetime import UTC, datetime

import duckdb
import pyarrow as pa

from ce_ingest.models import Survey
from ce_ingest.writers.base import Writer

log = logging.getLogger(__name__)

PROFILE_TABLE = "column_profile"
UNMATCHED_TABLE = "unmatched_codes"
VARIABLES_TABLE = "variables"  # ref tables, built by `ce-meta build`
CODES_TABLE = "codes"
# Columns the loader adds. The dictionary does not list them; the sidecars
# describe them from a fixed block, so they are never "undocumented".
LOADER_COLUMNS: frozenset[str] = frozenset(
    {"cuid", "mo_scope", "survey_year", "quarter", "quarter_code"}
)
WIDE_TABLES: frozenset[str] = frozenset({"fmli", "memi", "fmld", "memd"})
ID_COLUMNS: frozenset[str] = frozenset({"newid", "cuid"})
TOP_N = 20

TOP_VALUE = pa.struct([("value", pa.string()), ("count", pa.int64())])
PROFILE_SCHEMA = pa.schema(
    [
        ("dataset_id", pa.string()),
        ("survey", pa.string()),
        ("table", pa.string()),
        ("column_name", pa.string()),
        ("position", pa.int32()),
        ("data_type", pa.string()),
        ("row_count", pa.int64()),
        ("null_count", pa.int64()),
        ("null_share", pa.float64()),
        ("min", pa.string()),
        ("max", pa.string()),
        ("distinct_count", pa.int64()),
        ("top_values", pa.list_(TOP_VALUE)),  # null for ID-like columns
        ("undocumented", pa.bool_()),  # null until the dictionary is loaded
        ("profiled_at", pa.timestamp("us", tz="UTC")),
    ]
)

UNMATCHED_SCHEMA = pa.schema(
    [
        ("survey", pa.string()),
        ("table", pa.string()),
        ("column_name", pa.string()),
        ("value", pa.string()),
        ("row_count", pa.int64()),
    ]
)


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


STATS_BATCH = 100  # columns per aggregate query; bounds DuckDB's memory


def _basic_stats(
    con: duckdb.DuckDBPyConnection, source: str, columns: list[str]
) -> tuple[int, dict[str, tuple]]:
    """Row count and, per column, (non-null count, min, max), in column batches."""
    row_count = con.execute(f"select count(*) from {source}").fetchone()[0]
    stats: dict[str, tuple] = {}
    for start in range(0, len(columns), STATS_BATCH):
        batch = columns[start : start + STATS_BATCH]
        exprs = []
        for c in batch:
            q = _q(c)
            exprs += [f"count({q})", f"min({q})::varchar", f"max({q})::varchar"]
        row = con.execute(f"select {', '.join(exprs)} from {source}").fetchone()
        for i, c in enumerate(batch):
            stats[c] = tuple(row[3 * i : 3 * i + 3])
    return row_count, stats


def _values_unpivot(
    con: duckdb.DuckDBPyConnection, source: str, columns: list[str]
) -> tuple[dict[str, int], dict[str, list[dict]]]:
    """Wide tables, in one pass: unpivot to (column, value) and group.

    Gives each column's distinct count and its top values. Values are compared
    as text, which matches the distinct count of the typed values.
    """
    cols = ", ".join(f"{_q(c)}::varchar as {_q(c)}" for c in columns)
    rows = con.execute(
        f"""
        with u as (
            unpivot (select {cols} from {source})
            on columns(*) into name col value val
        ),
        g as (select col, val, count(*) as n from u group by all),
        r as (
            select *, row_number() over (partition by col order by n desc, val) as rk
            from g
        )
        select col, count(*),
               list({{'value': val, 'count': n}} order by n desc, val)
                 filter (where rk <= {TOP_N})
        from r group by col
        """
    ).fetchall()
    distinct = {col: n for col, n, _ in rows}
    tops = {col: top for col, _, top in rows}
    return distinct, tops


def _values_by_column(
    con: duckdb.DuckDBPyConnection, source: str, columns: list[str]
) -> tuple[dict[str, int], dict[str, list[dict]]]:
    """Item tables (few columns, many rows): one column at a time."""
    distinct: dict[str, int] = {}
    tops: dict[str, list[dict]] = {}
    for c in columns:
        q = _q(c)
        distinct[c] = con.execute(
            f"select count(distinct {q}) from {source}"
        ).fetchone()[0]
        rows = con.execute(
            f"select {q}::varchar as v, count(*) as n from {source} "
            f"where {q} is not null group by 1 order by n desc, v limit {TOP_N}"
        ).fetchall()
        tops[c] = [{"value": v, "count": n} for v, n in rows]
    return distinct, tops


def profile_table(writer: Writer, survey: Survey, table: str) -> pa.Table:
    """Profile one stored table and replace its rows in `column_profile`."""
    source = writer.duckdb_from(survey, table)
    if source is None:
        raise ValueError(f"{survey}/{table}: no stored partitions to profile")

    con = duckdb.connect()
    described = con.execute(f"describe select * from {source}").fetchall()
    columns = [name for name, *_ in described]
    types = {name: dtype for name, dtype, *_ in described}

    row_count, stats = _basic_stats(con, source, columns)
    if table in WIDE_TABLES:
        distinct, tops = _values_unpivot(con, source, columns)
    else:
        distinct, tops = _values_by_column(con, source, columns)

    now = datetime.now(UTC)
    rows = []
    for i, c in enumerate(columns):
        non_null, lo, hi = stats[c]
        nulls = row_count - non_null
        rows.append(
            {
                "dataset_id": f"{survey}_{table}",
                "survey": survey,
                "table": table,
                "column_name": c,
                "position": i,
                "data_type": types[c],
                "row_count": row_count,
                "null_count": nulls,
                "null_share": nulls / row_count if row_count else None,
                "min": lo,
                "max": hi,
                "distinct_count": distinct.get(c, 0),  # 0: every value null
                "top_values": None if c in ID_COLUMNS else tops.get(c, []),
                "undocumented": None,
                "profiled_at": now,
            }
        )
    result = pa.Table.from_pylist(rows, schema=PROFILE_SCHEMA)
    writer.replace_rows(PROFILE_TABLE, result, where={"survey": survey, "table": table})
    log.info("profiled %s/%s: %d columns", survey, table, len(columns))
    return result


# ── what needs the dictionary tables ─────────────────────────────────────────


def same_code(value: str, known: set[str]) -> bool:
    """A stored value matches a code as text, as a number ("100.0" = "100"), or a
    pattern code where n stands for any digit ("2nn" matches "201")."""
    if value in known:
        return True
    text = value[:-2] if value.endswith(".0") else value
    for k in known:
        if "n" in k and re.fullmatch(k.replace("n", r"\d"), text):
            return True
    try:
        number = float(value)
    except ValueError:
        return False
    for k in known:
        try:
            if float(k) == number:
                return True
        except ValueError:
            continue
    return False


def _covers(first: int | None, last: int | None, year: int) -> bool:
    return (first is None or first <= year) and (last is None or last >= year)


def mark_undocumented(writer: Writer, survey: Survey, release: int) -> int:
    """Fill `column_profile.undocumented` for one survey; returns the number flagged.

    A column is documented when the dictionary has it for the release, as a
    variable or as a variable's flag, or when the loader added it.
    """
    profile = writer.read_rows(PROFILE_TABLE)
    variables = writer.read_rows(VARIABLES_TABLE, "ref")
    if profile is None or variables is None:
        raise ValueError("column_profile and ref/variables must both be built first")
    documented: set[tuple[str, str]] = set()
    for v in variables.to_pylist():
        if not _covers(v["first_year"], v["last_year"], release):
            continue
        documented.add((v["table"], v["variable_name"]))
        if v["flag_name"]:
            documented.add((v["table"], v["flag_name"]))

    rows = profile.to_pylist()
    flagged = 0
    for r in rows:
        if r["survey"] != survey:
            continue
        known = (r["table"], r["column_name"]) in documented
        r["undocumented"] = not (known or r["column_name"] in LOADER_COLUMNS)
        flagged += r["undocumented"]
    writer.replace_table(
        "meta", PROFILE_TABLE, pa.Table.from_pylist(rows, schema=PROFILE_SCHEMA)
    )
    return flagged


def unmatched_codes(writer: Writer, survey: Survey, release: int) -> pa.Table:
    """Values in the data that are not in their column's code list.

    For each stored column that has a code list for `release`, counts the rows
    of every value the list does not hold, and replaces the survey's rows in
    `meta/unmatched_codes`. Nulls and empty strings are blanks, not codes.
    """
    codes = writer.read_rows(CODES_TABLE, "ref")
    if codes is None:
        raise ValueError("ref/codes must be built first")
    lists: dict[tuple[str, str], set[str]] = {}
    for c in codes.to_pylist():
        if _covers(c["first_year"], c["last_year"], release):
            lists.setdefault((c["table"], c["variable_name"]), set()).add(
                c["code_value"]
            )

    con = duckdb.connect()
    rows = []
    for stored_survey, table in writer.tables(survey):
        source = writer.duckdb_from(stored_survey, table)
        if source is None:
            continue
        columns = [
            name
            for name, *_ in con.execute(f"describe select * from {source}").fetchall()
        ]
        for column in columns:
            known = lists.get((table, column))
            if not known or column in ID_COLUMNS:
                continue
            q = _q(column)
            found = con.execute(
                f"select {q}::varchar as v, count(*) as n from {source} "
                f"where {q} is not null and {q}::varchar <> '' group by 1"
            ).fetchall()
            rows += [
                {
                    "survey": survey,
                    "table": table,
                    "column_name": column,
                    "value": value,
                    "row_count": n,
                }
                for value, n in sorted(found)
                if not same_code(value, known)
            ]
    result = pa.Table.from_pylist(rows, schema=UNMATCHED_SCHEMA)
    writer.replace_rows(UNMATCHED_TABLE, result, where={"survey": survey})
    return result


def finish_profile(
    writer: Writer, survey: Survey, release: int
) -> tuple[int, int] | None:
    """Run the two steps above when the dictionary tables exist.

    Returns (columns flagged undocumented, unmatched values), or None when
    `ref/codes` is not built yet.
    """
    if writer.read_rows(CODES_TABLE, "ref") is None:
        log.info("undocumented flags and unmatched_codes wait for `ce-meta build`")
        return None
    flagged = mark_undocumented(writer, survey, release)
    unmatched = unmatched_codes(writer, survey, release)
    return flagged, unmatched.num_rows
