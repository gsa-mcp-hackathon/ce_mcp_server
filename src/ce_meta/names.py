"""Finding a table or a column by the name a person or a model gives.

The sidecar set is searched first: it describes every loaded table, and its
reader already accepts a stored name or the BLS spelling in any casing. The
dictionary tables, once built, add the names that are not in the loaded data.
"""

import difflib
from collections.abc import Iterable

from ce_meta.catalog import Catalog
from ce_meta.errors import OutOfScope, UnknownTable, UnknownVariable
from ce_meta.sidecars import TABLE_ID_PREFIX, SidecarSet, Table

# Core tables first, in the order a reader meets them; then the detailed
# expenditure tables by name.
CORE_ORDER = ("fmli", "memi", "mtbi", "itbi", "itii")
DIARY_NAMES = "diary_variables"


def table_rank(table: str) -> tuple[int, str]:
    """Sort key that puts the core tables first."""
    if table in CORE_ORDER:
        return (CORE_ORDER.index(table), table)
    return (len(CORE_ORDER), table)


def ordered_tables(sidecars: SidecarSet) -> list[Table]:
    return sorted(sidecars.tables.values(), key=lambda t: table_rank(t.name))


def closest_names(name: str, candidates: Iterable[str], n: int = 3) -> list[str]:
    """Up to `n` candidates that look like `name`, best first."""
    pool = sorted(set(candidates))
    return difflib.get_close_matches(name.lower(), pool, n=n, cutoff=0.6)


def resolve_table(sidecars: SidecarSet, name: str) -> Table:
    """A table by its stored name or sidecar id, in any casing."""
    wanted = name.strip().lower()
    found = sidecars.table(wanted)
    if found is None:
        names = [t.name for t in sidecars.tables.values()]
        raise UnknownTable(
            name, closest_names(wanted.removeprefix(TABLE_ID_PREFIX), names)
        )
    return found


def _is_diary_name(catalog: Catalog, name: str) -> bool:
    if not catalog.has("ref", DIARY_NAMES):
        return False
    source = catalog.source("ref", DIARY_NAMES)
    rows = catalog.rows(
        f"select 1 from {source} where variable_name = ? limit 1", [name.lower()]
    )
    return bool(rows)


def resolve_name(
    sidecars: SidecarSet,
    catalog: Catalog,
    name: str,
    table: str | None = None,
    year: int | None = None,
) -> list[tuple[str, str]]:
    """Every (table, stored name) that carries `name`, core tables first.

    `year` is accepted for the dictionary lookup added with the dictionary
    tables; the sidecar set describes one release.
    """
    wanted = name.strip()
    tables = [resolve_table(sidecars, table)] if table else ordered_tables(sidecars)
    found = [(t.name, col.name) for t in tables if (col := t.column(wanted))]
    if found:
        return found
    if _is_diary_name(catalog, wanted):
        raise OutOfScope(name, "it is a Diary Survey variable")
    known = {c for t in tables for c in t.columns}
    raise UnknownVariable(name, closest_names(wanted, known), table)
