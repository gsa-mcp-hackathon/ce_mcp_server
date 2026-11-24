"""Reading a period: the survey years it touches, and years in the user's words."""

from __future__ import annotations

import re

_YEAR = re.compile(r"(?<!\d)(19[89]\d|20\d\d)(?!\d)")
_QUARTER = [
    re.compile(r"\bq([1-4])\s*(?:of\s*)?((?:19|20)\d\d)\b", re.I),
    re.compile(r"\b((?:19|20)\d\d)\s*-?\s*q([1-4])\b", re.I),
]
_ORDINAL = {"first": 1, "second": 2, "third": 3, "fourth": 4, "1st": 1, "2nd": 2,
            "3rd": 3, "4th": 4}  # fmt: skip
_QUARTER_WORDS = re.compile(
    r"\b(first|second|third|fourth|1st|2nd|3rd|4th)\s+quarter\s+(?:of\s+)?"
    r"((?:19|20)\d\d)\b",
    re.I,
)


def years_in(text: str | None) -> list[int]:
    """Plain years in the text, in order, once each."""
    if not text:
        return []
    seen: list[int] = []
    for m in _YEAR.finditer(text):
        year = int(m[1])
        if year not in seen:
            seen.append(year)
    return seen


def quarters_in(text: str | None) -> list[str]:
    """Quarters in the text, as '2025Q2'."""
    if not text:
        return []
    found: list[str] = []
    for m in _QUARTER[0].finditer(text):
        found.append(f"{m[2]}Q{m[1]}")
    for m in _QUARTER[1].finditer(text):
        found.append(f"{m[1]}Q{m[2]}")
    for m in _QUARTER_WORDS.finditer(text):
        found.append(f"{m[2]}Q{_ORDINAL[m[1].lower()]}")
    return list(dict.fromkeys(found))


def value_year(value: int | str) -> int | None:
    """The year of one period value: 2025, '2025Q2' or '2025-07'."""
    m = re.match(r"^\s*((?:19|20)\d\d)", str(value))
    return int(m[1]) if m else None


def period_years(period: dict | None, release: int) -> list[int]:
    """The survey years a node's period touches. Not stated: the release."""
    if not period or period.get("kind") == "not_stated":
        return [release]
    values = period.get("values") or []
    if period.get("kind") == "month_range" and len(values) == 2:
        first, last = value_year(values[0]), value_year(values[1])
        if first is not None and last is not None and first <= last:
            return list(range(first, last + 1))
    years = [y for v in values if (y := value_year(v)) is not None]
    return list(dict.fromkeys(years))
