"""Result models of the metadata service.

Every answer is a frozen model with a short `summary` a client can show as is
and the `notices` a reader must see with it. No model holds rows of a data
table: counts and descriptions only.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

Usage = Literal["measure", "grouping", "filter"]
GeoLevel = Literal["region", "division", "state", "psu"]
Support = Literal["reliable", "limited", "unsupported"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True)


class Notice(_Model):
    """Something a reader must know before relying on an answer.

    `set` and `table` come from the sidecar validator's report; `build` says a
    stored table that would add to the answer has not been built yet.
    """

    scope: Literal["set", "table", "build"]
    table: str | None = None
    text: str


class Fact(_Model):
    """One fact from a sidecar, with what the vocabulary says follows from it."""

    property: str
    value: Any
    implies: str | None = None


# ── describe_survey ──────────────────────────────────────────────────────────


class SurveyFacts(_Model):
    release: int | None
    target_population: str | None
    design: Fact | None
    weight: Fact | None  # the column and the table that carries it
    reference_period: Fact | None
    geography_levels: Fact | None
    breaks: Fact | None
    constraints: list[str]  # the master's context constraints
    not_held: list[str] = []  # what this server cannot answer from, and why
    summary: str
    notices: list[Notice] = []  # left out of an answer when empty


class WeightingMethod(_Model):
    weight: Fact | None
    replicate_weights: Fact | None
    variance: Fact | None
    reference_period: Fact | None
    summary: str
    notices: list[Notice] = []  # left out of an answer when empty


# ── list_datasets, describe_table ────────────────────────────────────────────


class DatasetInfo(_Model):
    """One stored table. Its dataset ID is `intrvw_<table>`."""

    table: str
    title: str | None  # the table's business name
    purpose: str | None = None  # one line; core tables only, to keep the list short
    row_represents: str | None  # from the master; None without a sidecar
    rows: int | None  # rows in the year's set: interviews for fmli only
    partitions: list[str] | None = None  # only when they differ from the list's
    has_sidecar: bool = True


class DatasetList(_Model):
    year: int
    partitions: list[str]  # "2025/1x": the partitions most tables hold
    loaded_at: datetime | None  # the latest load
    datasets: list[DatasetInfo]
    summary: str
    notices: list[Notice] = []  # left out of an answer when empty


class WeightRoute(_Model):
    parent: str  # the stored table that carries the weight
    parent_keys: list[str]
    child_keys: list[str]
    child_rows_optional: bool  # an interview with no rows here counts as zero
    note: str | None = None


class TableDetail(_Model):
    table: str
    sidecar: str | None  # ce-interview-fmli; None without a sidecar
    row_represents: str | None
    row_sentence: str | None  # "One row per consumer unit per quarterly interview."
    row_key: list[str]
    purpose: str | None
    limitations: str | None
    description_written: bool  # False for the generator's placeholder
    carries_weight: bool
    weight_route: WeightRoute | None
    same_unit_keys: list[str]  # e.g. ["cuid"]; groups rows, never a weight route
    period_columns: list[str]
    columns_by_role: dict[str, int]
    columns_needing_review: int
    summary: str
    notices: list[Notice] = []  # left out of an answer when empty


# ── search_variables, describe_variable ──────────────────────────────────────


class CodeEntry(_Model):
    value: str
    label: str | None
    first_year: int | None = None
    last_year: int | None = None


class VariableMatch(_Model):
    name: str
    source_name: str | None
    table: str
    description: str | None
    first_year: int | None = None  # None until the dictionary tables are built
    last_year: int | None = None  # None = still current
    in_loaded_data: bool = True
    role: str | None  # ce:role; None when not assigned or no sidecar
    unit: str | None  # ce:unit, VERIFY included
    usable_as: list[Usage]
    review: list[str]  # "role not assigned", "unit under review", "needs-review"
    matched_codes: list[CodeEntry] = []  # up to three codes whose label matched
    lacks: list[str] = []  # for a close match: the query words it does not hold


class VariableSearch(_Model):
    query: str
    matches: list[VariableMatch]
    total: int  # matches found before the limit was applied
    summary: str
    notices: list[Notice] = []  # left out of an answer when empty


class ProfileStats(_Model):
    rows: int | None
    null_share: float | None
    distinct_count: int | None
    min: str | None
    max: str | None
    undocumented_values: list[str] | None  # None until unmatched_codes is built


class SidecarFacts(_Model):
    id: str
    facts: dict[str, Any]
    needs_review: bool
    unverified: list[str]  # properties whose value is still VERIFY


class ImputedSet(_Model):
    base: str | None  # the reported column the versions were imputed from
    versions: list[str]  # the five version columns, when they are columns
    mean: str | None  # the column holding the mean of the versions
    version_column: str | None = None  # numbers the versions when they are rows


class VariableDetail(VariableMatch):
    business_name: str | None
    data_type: str | None
    codes: list[CodeEntry]  # at most code_limit; long labels are cut
    codes_total: int  # codes the column has
    codes_matched: int | None = None  # codes matching code_query, when one is given
    calendar_pair: str | None  # the other quarter column of a pair
    flag_column: str | None  # the column whose ce:flagFor names this one
    imputed_set: ImputedSet | None
    profile: ProfileStats | None
    sidecar: SidecarFacts | None
    caveats: list[str]  # the vocabulary's implies text, one per fact
    also_in: list[str]  # other tables holding the same name
    sources: list[str]
    summary: str
    notices: list[Notice] = []  # left out of an answer when empty


# ── list_geographies ─────────────────────────────────────────────────────────


class GeographyEntry(_Model):
    code: str
    label: str | None
    parent_state: str | None = None
    parent_division: str | None = None
    parent_region: str | None = None
    interviews: int | None = None  # unweighted, in the year's set
    note: str | None = None  # e.g. "spans NY, NJ, PA", "not a region"


class GeographyList(_Model):
    level: GeoLevel
    year: int
    table: str | None  # where the level's column is, e.g. fmli
    column: str | None  # None when no sidecar column carries the level
    support: Support | None
    entries: list[GeographyEntry]
    unlisted_interviews: int | None  # interviews whose value is blank or not a code
    summary: str
    notices: list[Notice] = []  # left out of an answer when empty
