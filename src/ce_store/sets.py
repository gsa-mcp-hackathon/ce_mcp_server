"""Which stored partitions make up one survey year."""

from typing import Literal

Survey = Literal["intrvw", "diary"]


def survey_year_set(survey: Survey, year: int) -> list[tuple[int, str]]:
    """`(survey_year, quarter_code)` partitions of one release year.

    Interview year Y is Y's `1x`, `2`, `3`, `4` plus Y+1's `1`; Diary year Y is
    Y's four quarters.
    """
    if survey == "intrvw":
        return [(year, "1x"), (year, "2"), (year, "3"), (year, "4"), (year + 1, "1")]
    if survey == "diary":
        return [(year, q) for q in ("1", "2", "3", "4")]
    raise ValueError(f"unknown survey {survey!r}")


def survey_year_filter(survey: Survey, year: int) -> str:
    """The same set as a SQL predicate, for DuckDB and Spark."""
    by_year: dict[int, list[str]] = {}
    for y, code in survey_year_set(survey, year):
        by_year.setdefault(y, []).append(code)
    parts = [
        f"(survey_year = {y} AND quarter_code IN ({', '.join(repr(c) for c in codes)}))"
        for y, codes in sorted(by_year.items())
    ]
    return "(" + " OR ".join(parts) + ")"
