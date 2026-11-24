"""What normalize, the check and derivation read: the sidecar set and what is loaded.

Built once from the metadata service (Plan 3), which already holds the sidecar
set, the validator's report and the catalog of stored tables. Code in this
package reads sidecar facts only through it (D19).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property

from ce_meta.catalog import SURVEY
from ce_meta.service import MetadataService
from ce_meta.sidecars import TABLE_ID_PREFIX, Column, Report, SidecarSet, Table
from ce_store import survey_year_set

# Rows that are one item in one reference month: the reference year and month
# columns say which year a row belongs to (Plan 4, measure_kind monthly_rows).
MONTHLY_ROWS = frozenset(
    {"expenditure_item_month", "income_item_month", "imputed_income_item_month"}
)
DETAILED_EXPENDITURE = "expn_record"
MEMBER_ROWS = "member_interview"
LOADED_STATUSES = ("loaded", "loaded_with_warnings")


@dataclass
class CaptureContext:
    sidecars: SidecarSet
    report: Report
    release: int  # the survey year a request with no period is read as
    loaded_partitions: set[tuple[int, str]] = field(default_factory=set)
    service: MetadataService | None = None  # for the lookups of the server path

    @classmethod
    def from_service(cls, service: MetadataService) -> CaptureContext:
        return cls(
            sidecars=service.sidecars,
            report=service.report,
            release=service.release,
            loaded_partitions=_loaded_partitions(service),
            service=service,
        )

    # ── tables ───────────────────────────────────────────────────────────────

    def table(self, name: str) -> Table | None:
        return self.sidecars.table(name.strip().lower()) if name else None

    @cached_property
    def listed(self) -> set[str]:
        master = self.sidecars.master
        return {
            str(t["sidecar"]).removeprefix(TABLE_ID_PREFIX)
            for t in (master.tables if master else [])
        }

    def covered(self, name: str) -> bool:
        """Capture covers a table the master lists whose sidecar is usable (D15)."""
        return (
            name in self.listed
            and self.sidecars.table(name) is not None
            and self.report.usable(name)
        )

    @cached_property
    def covered_tables(self) -> list[str]:
        from ce_meta.names import table_rank

        return sorted((t for t in self.listed if self.covered(t)), key=table_rank)

    @cached_property
    def weight(self) -> tuple[str, str]:
        """(table, column) of the sampling weight, from the master."""
        master = self.sidecars.master
        fact = (master.facts.get("ce:weight") if master else None) or {}
        table = str(fact.get("table", "ce-interview-fmli")).removeprefix(
            TABLE_ID_PREFIX
        )
        return table, str(fact.get("column", "finlwt21"))

    def column(self, table: str, name: str) -> Column | None:
        found = self.table(table)
        return found.column(name) if found else None

    def row_represents(self, table: str) -> str | None:
        found = self.table(table)
        return found.facts.get("ce:rowRepresents") if found else None

    def route(self, table: str) -> dict | None:
        """The weightFrom-parent relationship whose child is `table`."""
        master = self.sidecars.master
        for rel in master.relationships if master else []:
            child = str(rel.get("child", "")).removeprefix(TABLE_ID_PREFIX)
            if child == table and rel.get("weightFrom") == "parent":
                return rel
        return None

    def reaches_weight(self, table: str, seen: frozenset[str] = frozenset()) -> bool:
        if table == self.weight[0]:
            return True
        if table in seen:
            return False
        rel = self.route(table)
        if rel is None:
            return False
        parent = str(rel.get("parent", "")).removeprefix(TABLE_ID_PREFIX)
        return self.reaches_weight(parent, seen | {table})

    # ── what is loaded ───────────────────────────────────────────────────────

    def year_loaded(self, year: int) -> bool:
        """A survey year is loaded when every file of its set is (D22)."""
        return set(survey_year_set(SURVEY, year)) <= self.loaded_partitions

    @cached_property
    def loaded_years(self) -> list[int]:
        years = {y for y, _ in self.loaded_partitions} | {
            y - 1 for y, _ in self.loaded_partitions
        }
        return sorted(y for y in years if self.year_loaded(y))

    def versions(self, tables: list[str]) -> dict:
        """The sidecar versions a record was written against."""
        master = self.sidecars.master
        vocab = self.sidecars.vocabulary
        refs = []
        for name in tables:
            found = self.table(name)
            if found is not None:
                refs.append(
                    {"name": found.name, "id": found.id, "version": found.version}
                )
        return {
            "master": {"id": master.id, "version": master.version} if master else None,
            "tables": refs,
            "vocabulary": {"id": vocab.id, "version": vocab.version} if vocab else None,
        }


def _loaded_partitions(service: MetadataService) -> set[tuple[int, str]]:
    """Partitions of the weight table loaded without failure, from the registry."""
    catalog = service.catalog
    if not catalog.has("meta", "dataset_registry"):
        return set()
    weight_table = "fmli"
    master = service.sidecars.master
    if master is not None:
        fact = master.facts.get("ce:weight") or {}
        weight_table = str(fact.get("table", weight_table)).removeprefix(
            TABLE_ID_PREFIX
        )
    rows = catalog.rows(
        f"""
        select distinct survey_year, quarter_code
        from {catalog.source("meta", "dataset_registry")}
        where survey = ? and "table" = ? and status in (?, ?)
        """,
        [SURVEY, weight_table, *LOADED_STATUSES],
    )
    return {(r["survey_year"], r["quarter_code"]) for r in rows}
