"""Read the BLS CE data dictionary (XLSX): variables and code lists.

`read_history` reads every row of the Variables and Codes sheets, for both
surveys, with the years each row was in force. `read_dictionary` is a filter
over it: one file as it stands in one release. The sidecar generator uses the
second and the dictionary tables (`ce-meta build`) the first, so a sidecar's
code list and the `codes` table come from one reader.

Names are normalized the way the loader stores columns (`ce_ingest.normalize`),
so `FAM__IZE` in the dictionary matches the stored `fam__ize`.
"""

import functools
from dataclasses import dataclass, field
from pathlib import Path

from ce_ingest.normalize import normalize_name


@dataclass(frozen=True)
class Variable:
    name: str  # normalized, as stored
    source_name: str  # BLS spelling
    description: str
    formula: str | None
    flag: str | None  # normalized name of its flag column
    section: str | None


@dataclass(frozen=True)
class Code:
    value: str
    label: str


@dataclass
class FileDictionary:
    """One dictionary file (FMLI, MEMI, RNT, ...) as it stands in one release."""

    file: str
    release: int
    variables: dict[str, Variable] = field(default_factory=dict)
    codes: dict[str, list[Code]] = field(default_factory=dict)

    def flag_of(self) -> dict[str, str]:
        """Flag column name -> the value column it qualifies."""
        return {v.flag: v.name for v in self.variables.values() if v.flag}


@dataclass(frozen=True)
class VariableRange:
    """One row of the Variables sheet: a variable over a range of years."""

    survey: str  # INTERVIEW or DIARY
    file: str  # FMLI, MEMI, RNT, ...
    variable: Variable
    first_year: int | None
    last_year: int | None  # None = still current


@dataclass(frozen=True)
class CodeRange:
    """One row of the Codes sheet: a code of a variable over a range of years."""

    survey: str
    file: str
    variable: str  # normalized, as stored
    code: Code
    first_year: int | None
    last_year: int | None  # None = still current


@dataclass(frozen=True)
class DictionaryHistory:
    """Every row of both sheets, in sheet order."""

    variables: tuple[VariableRange, ...]
    codes: tuple[CodeRange, ...]


def _year(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def covers(first: int | None, last: int | None, year: int) -> bool:
    """In force in `year`: started by it and not ended before it."""
    return (first is None or first <= year) and (last is None or last >= year)


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


@functools.lru_cache(maxsize=4)
def _read_history(path: str, modified: float) -> DictionaryHistory:
    import openpyxl  # loader extra; imported here so the MCP server never needs it

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    # sheet and header names carry stray spaces ("Codes ", "Formula ")
    sheets = {name.strip(): wb[name] for name in wb.sheetnames}

    variables: list[VariableRange] = []
    rows = sheets["Variables"].iter_rows(values_only=True)
    head = {_text(h): i for i, h in enumerate(next(rows))}
    for r in rows:
        source = _text(r[head["Variable Name"]])
        if not source:
            continue
        flag = _text(r[head["Flag name"]])
        variables.append(
            VariableRange(
                survey=_text(r[head["Survey"]]).upper(),
                file=_text(r[head["File"]]).upper(),
                variable=Variable(
                    name=normalize_name(source),
                    source_name=source,
                    description=_text(r[head["Variable description"]]),
                    formula=_text(r[head["Formula"]]) or None,
                    flag=normalize_name(flag) if flag else None,
                    section=_text(r[head["Section description"]]) or None,
                ),
                first_year=_year(r[head["First year"]]),
                last_year=_year(r[head["Last year"]]),
            )
        )

    codes: list[CodeRange] = []
    rows = sheets["Codes"].iter_rows(values_only=True)
    head = {_text(h): i for i, h in enumerate(next(rows)) if h is not None}
    for r in rows:
        variable = _text(r[head["Variable"]])
        if not variable:
            continue
        codes.append(
            CodeRange(
                survey=_text(r[head["Survey"]]).upper(),
                file=_text(r[head["File"]]).upper(),
                variable=normalize_name(variable),
                code=Code(
                    value=_text(r[head["Code value"]]),
                    label=_text(r[head["Code description"]]),
                ),
                first_year=_year(r[head["First year"]]),
                last_year=_year(r[head["Last year"]]),
            )
        )
    wb.close()
    return DictionaryHistory(tuple(variables), tuple(codes))


def read_history(path: Path) -> DictionaryHistory:
    """Every variable and code of both surveys, with its range of years.

    The workbook is read once per file version: generating many sidecars in
    one run does not read it again.
    """
    path = Path(path)
    return _read_history(str(path.resolve()), path.stat().st_mtime)


def read_dictionary(path: Path, file: str, release: int) -> FileDictionary:
    """Variables and codes of one dictionary file, current for `release`."""
    history = read_history(path)
    out = FileDictionary(file=file.upper(), release=release)
    for row in history.variables:
        if row.file == out.file and covers(row.first_year, row.last_year, release):
            out.variables[row.variable.name] = row.variable
    for row in history.codes:
        if row.file != out.file or not covers(row.first_year, row.last_year, release):
            continue
        listed = out.codes.setdefault(row.variable, [])
        if row.code.value not in {c.value for c in listed}:
            listed.append(row.code)
    return out
