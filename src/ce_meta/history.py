"""The dictionary tables in memory: what the BLS dictionary says, year by year.

`ref/variables` and `ref/codes` are small (a few thousand rows each), so the
service reads them once and answers from memory. They add to an answer what the
sidecars, which describe one release, cannot give: the years a variable or a
code was in force, and variables that are not in the loaded data.
"""

from dataclasses import dataclass

from ce_meta.catalog import Catalog

VARIABLES = "variables"
CODES = "codes"


@dataclass(frozen=True)
class VariableRow:
    table: str
    name: str  # as stored
    source_name: str  # as BLS spells it
    description: str | None
    data_type: str | None
    first_year: int | None
    last_year: int | None  # None = still current
    flag_name: str | None


@dataclass(frozen=True)
class CodeRow:
    value: str
    label: str | None
    first_year: int | None
    last_year: int | None


def covers(first: int | None, last: int | None, year: int) -> bool:
    """In force in `year`: started by it and not ended before it."""
    return (first is None or first <= year) and (last is None or last >= year)


class History:
    def __init__(self, variables: list[VariableRow], codes: dict) -> None:
        self._variables: dict[tuple[str, str], list[VariableRow]] = {}
        self._by_name: dict[str, list[tuple[str, str]]] = {}
        for row in variables:
            key = (row.table, row.name)
            if key not in self._variables:
                for spelling in {row.name, row.source_name.lower()}:
                    self._by_name.setdefault(spelling, []).append(key)
            self._variables.setdefault(key, []).append(row)
        self._codes: dict[tuple[str, str], list[CodeRow]] = codes
        firsts = [r.first_year for r in variables if r.first_year is not None]
        self.first_year: int | None = min(firsts, default=None)

    @property
    def tables(self) -> set[str]:
        return {table for table, _ in self._variables}

    def keys(self) -> list[tuple[str, str]]:
        """Every (table, variable) the dictionary has, in table and name order."""
        return sorted(self._variables)

    def ranges(self, table: str, name: str) -> list[VariableRow]:
        return self._variables.get((table, name), [])

    def covering(self, table: str, name: str, year: int) -> VariableRow | None:
        """The variable as it stood in `year`, if it existed then."""
        for row in self.ranges(table, name):
            if covers(row.first_year, row.last_year, year):
                return row
        return None

    def latest(self, table: str, name: str) -> VariableRow | None:
        rows = self.ranges(table, name)
        return max(rows, key=lambda r: r.first_year or 0) if rows else None

    def find(self, name: str, table: str | None = None) -> list[tuple[str, str]]:
        """Every (table, stored name) with that stored name or BLS spelling."""
        found = self._by_name.get(name.strip().lower(), [])
        return [key for key in found if table is None or key[0] == table]

    def codes(self, table: str, name: str, year: int | None = None) -> list[CodeRow]:
        """A variable's codes: in force in `year`, or all of them."""
        rows = self._codes.get((table, name), [])
        if year is None:
            return rows
        return [c for c in rows if covers(c.first_year, c.last_year, year)]

    def years(self, table: str, name: str) -> list[int]:
        """The first year of each range a variable has, for a "try" hint."""
        return sorted({r.first_year for r in self.ranges(table, name) if r.first_year})


def load_history(catalog: Catalog) -> History | None:
    """Read the dictionary tables, or None when they are not built yet."""
    if not (catalog.has("ref", VARIABLES) and catalog.has("ref", CODES)):
        return None
    variables = [
        VariableRow(
            table=r["table"],
            name=r["variable_name"],
            source_name=r["source_name"],
            description=r["description"],
            data_type=r["data_type"],
            first_year=r["first_year"],
            last_year=r["last_year"],
            flag_name=r["flag_name"],
        )
        for r in catalog.rows(
            f"""
            select "table", variable_name, source_name, description, data_type,
                   first_year, last_year, flag_name
            from {catalog.source("ref", VARIABLES)}
            order by "table", variable_name, first_year
            """
        )
    ]
    codes: dict[tuple[str, str], list[CodeRow]] = {}
    for r in catalog.rows(
        f"""
        select "table", variable_name, code_value, code_label, first_year, last_year
        from {catalog.source("ref", CODES)}
        order by "table", variable_name, code_value, first_year
        """
    ):
        codes.setdefault((r["table"], r["variable_name"]), []).append(
            CodeRow(r["code_value"], r["code_label"], r["first_year"], r["last_year"])
        )
    return History(variables, codes)
