"""Partition types of the stored tables, and how to read one with DuckDB.

`survey_year` and `quarter_code` live only in the folder names. Readers must
give their types explicitly: inferred from names alone, a table with no `1x`
partition (every Diary table) would read `quarter_code` as an integer.
"""

from pathlib import Path

HIVE_TYPES: dict[str, str] = {"survey_year": "INTEGER", "quarter_code": "VARCHAR"}


def duckdb_source(table_dir: Path) -> str:
    """A DuckDB `read_parquet(...)` expression for one stored table."""
    types = ", ".join(f"'{k}': '{v}'" for k, v in HIVE_TYPES.items())
    glob = (table_dir / "survey_year=*" / "quarter_code=*" / "*.parquet").as_posix()
    return (
        f"read_parquet('{glob}', hive_partitioning = true, union_by_name = true, "
        f"hive_types = {{{types}}})"
    )
