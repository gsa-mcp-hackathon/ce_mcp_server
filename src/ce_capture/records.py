"""The shapes of the request record, the follow-up patch and the resolved record.

These models are the one source of the three JSON Schemas Plan 4 names:
`ModelOutput` is what a model writes (the server-side model's output format and
the input of `submit_request_record`), `RequestRecord` is the stored record, and
`ResolvedRecord` is what derivation writes. The `.schema.json` files under
`schemas/` are generated from them (`ce-capture schemas`), and a test fails if
they fall behind.

Records are stored as plain JSON. Code builds every field a model does not
write, so only the model's part can be malformed; the capture check validates
a stored record against `RequestRecord` again when Plan 5 calls it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"
SCHEMA_DIR = Path(__file__).parent / "schemas"

Statistic = Literal["mean", "total", "count", "share", "ratio", "difference"]
EstimateStatistic = Literal["mean", "total", "count", "share"]
CombineStatistic = Literal["ratio", "difference"]
Comparison = Literal["eq", "ne", "in", "not_in", "lt", "le", "gt", "ge", "between"]
PeriodKind = Literal["year", "quarter", "month_range", "not_stated"]
ReasonCode = Literal[
    "not_statistical",
    "table_not_covered",
    "column_not_found",
    "statistic_not_supported",
    "outside_survey",
    "other",
]
Status = Literal["captured", "needs_clarification", "not_captured", "check_failed"]
CapturePath = Literal["server_model", "client_model"]
TextSource = Literal["user_text", "earlier_text"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ── what a model writes ──────────────────────────────────────────────────────


class ColumnRef(_Strict):
    table: str = Field(description="Stored table name, as a lookup tool gave it.")
    column: str = Field(description="Stored column name, as a lookup tool gave it.")


FilterValue = str | int | float | list[str | int | float]


class Filter(ColumnRef):
    comparison: Comparison
    value: FilterValue = Field(
        description="A code or a number; a list for in, not_in and between. "
        "Codes come from describe_variable."
    )


class Period(_Strict):
    text: str | None = Field(
        None,
        description="The user's words for the period, copied exactly; "
        "null when the user named none.",
    )
    kind: PeriodKind
    values: list[int | str] = Field(
        default_factory=list,
        description="Years (2025), quarters ('2025Q2') or the first and last "
        "month of a range ('2025-07', '2025-09'). Empty when not stated.",
    )


class _Choice(_Strict):
    evidence: str = Field(description="The user's words that state the choice.")


class WeightedChoice(_Choice):
    value: bool


class TimeBasisChoice(_Choice):
    value: Literal["year", "quarter", "month"]


class GapChoice(_Choice):
    value: Literal["as_released", "adjusted"]


class MissingCodesChoice(_Choice):
    value: Literal["removed", "kept"]


class ImputationChoice(_Choice):
    value: Literal[
        "five_versions_averaged",
        "version_1",
        "version_2",
        "version_3",
        "version_4",
        "version_5",
    ]


class CountUnitChoice(_Choice):
    value: Literal["consumer_unit", "interview", "member"]


class StandardErrorChoice(_Choice):
    value: bool


class Stated(_Strict):
    """Choices the user's words reveal. A choice not stated is left out."""

    weighted: WeightedChoice | None = None
    time_basis: TimeBasisChoice | None = None
    gap_2025: GapChoice | None = None
    missing_codes: MissingCodesChoice | None = None
    imputation: ImputationChoice | None = None
    count_unit: CountUnitChoice | None = None
    standard_error: StandardErrorChoice | None = None


STATED_FIELDS = tuple(Stated.model_fields)


class EstimateNode(_Strict):
    id: str = Field(description="Unique within the request, such as n1.")
    kind: Literal["estimate"]
    statistic: EstimateStatistic
    measure: ColumnRef | None = Field(
        None, description="The column summarized; null for a count or a share."
    )
    group_by: list[ColumnRef] = Field(
        default_factory=list,
        description="Columns that define subgroups: on the measure's table or fmli.",
    )
    filters: list[Filter] = Field(default_factory=list)
    period: Period
    stated: Stated = Field(default_factory=Stated)


class CombineInputs(_Strict):
    numerator: str | None = Field(None, description="For a ratio: a node ID.")
    denominator: str | None = Field(None, description="For a ratio: a node ID.")
    first: str | None = Field(None, description="For a difference: a node ID.")
    second: str | None = Field(
        None, description="For a difference: the node subtracted."
    )


class CombineNode(_Strict):
    id: str
    kind: Literal["combine"]
    statistic: CombineStatistic
    inputs: CombineInputs
    stated: Stated = Field(default_factory=Stated)


Node = Annotated[EstimateNode | CombineNode, Field(discriminator="kind")]


class Answer(_Strict):
    asks: str = Field(description="A phrase of the user's message, copied exactly.")
    node: str = Field(description="The ID of the node that answers it.")


class Ambiguity(_Strict):
    phrase: str = Field(description="The user's words that can be read more ways.")
    options: list[str]
    chosen: str
    ask_user: bool = Field(
        False,
        description="True only when the readings give materially different numbers "
        "or change whether the request can be answered.",
    )
    question: str | None = Field(None, description="The question to put to the user.")


class Unresolved(_Strict):
    phrase: str = Field(description="The user's words that matched nothing.")
    reason: str


# ── a follow-up patch ────────────────────────────────────────────────────────
#
# For "now do the same by state" a model writes changes to the earlier record's
# nodes, not the nodes again. `node: "*"` applies a change to every estimate node.


class SetGroupBy(_Strict):
    op: Literal["set_group_by"]
    node: str
    group_by: list[ColumnRef]


class AddFilter(_Strict):
    op: Literal["add_filter"]
    node: str
    filter: Filter


class RemoveFilter(_Strict):
    op: Literal["remove_filter"]
    node: str
    table: str
    column: str


class SetPeriod(_Strict):
    op: Literal["set_period"]
    node: str
    period: Period


class SetStatistic(_Strict):
    op: Literal["set_statistic"]
    node: str
    statistic: Statistic


class SetMeasure(_Strict):
    op: Literal["set_measure"]
    node: str
    measure: ColumnRef | None


class SetStated(_Strict):
    op: Literal["set_stated"]
    node: str
    field: Literal[
        "weighted",
        "time_basis",
        "gap_2025",
        "missing_codes",
        "imputation",
        "count_unit",
        "standard_error",
    ]
    value: Any
    evidence: str


class RemoveStated(_Strict):
    op: Literal["remove_stated"]
    node: str
    field: str


class AddNode(_Strict):
    op: Literal["add_node"]
    add: Node


class RemoveNode(_Strict):
    op: Literal["remove_node"]
    node: str


Change = Annotated[
    SetGroupBy
    | AddFilter
    | RemoveFilter
    | SetPeriod
    | SetStatistic
    | SetMeasure
    | SetStated
    | RemoveStated
    | AddNode
    | RemoveNode,
    Field(discriminator="op"),
]


class ModelOutput(_Strict):
    """What a model writes. Code fills every other field of the record."""

    nodes: list[Node] = Field(
        default_factory=list,
        description="The statistics asked for. Leave empty for a follow-up written "
        "as a patch, or for a message that is not a statistics request.",
    )
    patch: list[Change] | None = Field(
        None,
        description="For a follow-up only: changes to the earlier record's nodes.",
    )
    answers: list[Answer] = Field(default_factory=list)
    ambiguities: list[Ambiguity] = Field(default_factory=list)
    unresolved: list[Unresolved] = Field(default_factory=list)
    reason_code: ReasonCode | None = Field(
        None, description="Only when the message cannot be captured; with no nodes."
    )
    reason: str | None = None


# ── what code adds to make the stored record ─────────────────────────────────


class Source(_Strict):
    user_text: str
    session_id: str
    turn: int
    user_ref: str | None = None
    follows_request_id: str | None = None
    answers_request_id: str | None = None
    corrects_request_id: str | None = None
    earlier_text: str | None = Field(
        None,
        description="The linked record's text, so a follow-up or an answer stands "
        "alone.",
    )


class CaptureInfo(_Strict):
    path: CapturePath
    agent: str
    model: str | None
    attempts: int
    status: Status
    reason_code: ReasonCode | None = None
    reason: str | None = None
    questions: list[str] = Field(
        default_factory=list, description="For needs_clarification: what to ask."
    )
    lookups: int | None = Field(None, description="Lookup calls the model made.")


class SidecarRef(_Strict):
    id: str
    version: str


class TableRef(SidecarRef):
    name: str


class SidecarVersions(_Strict):
    master: SidecarRef | None
    tables: list[TableRef]
    vocabulary: SidecarRef | None


class Fix(_Strict):
    node: str | None
    field: str
    from_: Any = Field(alias="from")
    to: Any
    rule: str

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Span(_Strict):
    """Where code found a phrase the model quoted (D24)."""

    where: str = Field(description="Which phrase: a node field, an answer, ...")
    text: str = Field(description="The exact span of the text.")
    source: TextSource
    start: int
    end: int


class CheckError(_Strict):
    part: str
    node: str | None = None
    field: str | None = None
    message: str
    suggestions: list[str] = Field(default_factory=list)


class CheckResult(_Strict):
    passed: bool
    errors: list[CheckError] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)


class CarriedOver(_Strict):
    from_request_id: str
    unchanged: list[str]
    changed: list[str]
    added: list[str]
    removed: list[str]


class RequestRecord(_Strict):
    schema_version: str
    request_id: str
    captured_at: str
    source: Source
    capture: CaptureInfo
    sidecars: SidecarVersions
    nodes: list[Node]
    answers: list[Answer]
    ambiguities: list[Ambiguity]
    unresolved: list[Unresolved]
    carried_over: CarriedOver | None = None
    fixes: list[Fix]
    evidence: list[Span]
    check: CheckResult
    submitted: Any = Field(
        None,
        description="What the model wrote, kept when it did not match the schema.",
    )


# ── the resolved record ──────────────────────────────────────────────────────

Origin = Literal["stated", "defaulted", "derived"]


class Entry(_Strict):
    value: Any
    source: Origin


class YearSet(_Strict):
    survey_year: int
    files: list[dict[str, Any]]
    source: Origin


class UnitEntry(_Strict):
    measure: str | None = None
    result: str | None
    source: Origin


class ResolvedEstimate(_Strict):
    id: str
    kind: Literal["estimate"]
    year_sets: list[YearSet]
    measure_kind: Entry | None = None
    columns: Entry | None = None
    weight_route: Entry | None = None
    reference_rows: Entry | None = None
    interview_filters: Entry | None = None
    within_year: Entry | None = None
    weight: Entry
    time_basis: Entry | None = None
    unit: UnitEntry | None = None
    missing_codes: Entry | None = None
    imputation: Entry | None = None
    standard_error: Entry
    count_unit: Entry | None = None
    count_method: Entry | None = None
    geography_level: Entry | None = None
    gap_2025: Entry | None = None


class ResolvedInput(_Strict):
    node: str
    unit: str | None


class ResolvedCombine(_Strict):
    id: str
    kind: Literal["combine"]
    statistic: CombineStatistic
    inputs: dict[str, ResolvedInput]
    conversion: Entry
    standard_error: Entry
    unit: UnitEntry


class ResolvedRecord(_Strict):
    resolved_id: str
    request_id: str
    derived_at: str
    defaults_version: str
    sidecars: SidecarVersions
    nodes: list[
        Annotated[ResolvedEstimate | ResolvedCombine, Field(discriminator="kind")]
    ]
    flags: list[str]


# ── dumping and the schema files ─────────────────────────────────────────────


def dump(model: BaseModel) -> Any:
    """A model as stored JSON: a choice not stated is left out of `stated`."""
    data = model.model_dump(mode="json", by_alias=True)
    return _prune_stated(data)


def _prune_stated(data: Any) -> Any:
    if isinstance(data, dict):
        out = {}
        for key, value in data.items():
            if key == "stated" and isinstance(value, dict):
                out[key] = {k: v for k, v in value.items() if v is not None}
            else:
                out[key] = _prune_stated(value)
        return out
    if isinstance(data, list):
        return [_prune_stated(v) for v in data]
    return data


SCHEMAS: dict[str, type[BaseModel]] = {
    "model-output.schema.json": ModelOutput,
    "request-record.schema.json": RequestRecord,
    "resolved-record.schema.json": ResolvedRecord,
}


def schema_text(model: type[BaseModel]) -> str:
    schema = model.model_json_schema(by_alias=True)
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"ce-capture/{model.__name__}/{SCHEMA_VERSION}",
        **schema,
    }
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def write_schemas(folder: Path = SCHEMA_DIR) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for name, model in SCHEMAS.items():
        path = folder / name
        path.write_text(schema_text(model))
        written.append(path)
    return written
