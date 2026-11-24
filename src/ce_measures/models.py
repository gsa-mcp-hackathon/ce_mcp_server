"""The values passed between the steps of request processing.

All are frozen pydantic models. A rule is a row (`Rule`), not code: adding a
rule is adding a row to a table, and `verdict.evaluate` is the only reader.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

Level = Literal["pass", "warn", "substitute", "block"]
LEVEL_ORDER: dict[str, int] = {"pass": 0, "warn": 1, "substitute": 2, "block": 3}
Op = Literal[
    "eq", "ne", "in", "not_in", "has", "lacks", "missing", "present", "true", "false"
]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)


class Node(_Frozen):
    """One node, read from both records and joined by `id`."""

    id: str
    kind: Literal["estimate", "combine"]
    asked: dict[str, Any]  # the node in the request record
    resolved: dict[str, Any]  # the node in the resolved record


class NodeFacts(_Frozen):
    """Everything a node's rules may read, gathered once (`facts.collect`)."""

    columns: dict[str, dict[str, Any]]  # "measure", "group_by.0", "filters.0"
    master: dict[str, Any]
    flags: list[str]  # the codes of the flags that name this node
    flag_text: list[str]  # the same flags, whole
    derived: dict[str, Any]


class Test(_Frozen):
    """One condition of a rule. A test on a list holds when it holds for any member."""

    on: str  # a path: "columns.measure.ce:scale", "resolved.weight.value"
    op: Op
    value: Any = None


def T(on: str, op: Op, value: Any = None) -> Test:  # noqa: N802 - a table cell
    """A test, written short so a rule table reads as a table."""
    return Test(on=on, op=op, value=value)


class Rule(_Frozen):
    id: str
    level: Literal["block", "warn", "substitute"]
    when: list[Test]  # all must hold
    message: str  # may name {column}, {property}, {table}
    target: str | None = None  # for a substitute: what runs instead
    each: Literal["used", "group_by"] | None = None
    """Evaluate once per column the node uses, or per grouping column; the
    paths `column.*` then read that column, and a message names it."""
    property: str | None = None
    """The property the message names: a literal such as "ce:topcoded", or a
    path such as "column.review_property" whose value is used."""
    detail: str | None = None  # a path whose value fills {detail} in the message


class Message(_Frozen):
    level: Level
    rule: str
    column: str | None = None
    property: str | None = None
    text: str
    inputs: list[str] | None = Field(default=None, alias="from")

    def dump(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)


class Verdict(_Frozen):
    level: Level
    method: str | None  # the do_* method that runs, or would run
    weighted: bool
    substituted_from: str | None = None
    messages: list[Message] = []
    notes: list[str] = []


class GroupEstimate(_Frozen):
    group: dict[str, str | None]  # grouping column → code; empty when ungrouped
    value: float | None
    standard_error: float | None = None
    interviews: int  # rows of the frame used
    units: int  # distinct cuid used

    @property
    def key(self) -> tuple:
        return tuple(sorted(self.group.items()))


class Estimate(_Frozen):
    groups: list[GroupEstimate]
    unit: str | None
    removed_missing: int = 0  # rows of the measure's table removed for a code or flag
    removed_null_mo_scope: int = 0
    removed_null_value: int = 0  # interviews whose value is null
    dropped_no_rows: int = 0  # interviews dropped: a required table had no rows
    standard_error_method: str | None = None
    notes: list[str] = []
    # per group key: the 44 replicate estimates, kept for the combine nodes
    _replicates: dict[tuple, list[float]] = PrivateAttr(default_factory=dict)
    _variance: tuple[int, float] | None = PrivateAttr(default=None)

    def with_replicates(
        self, replicates: dict[tuple, list[float]], parameters: tuple[int, float]
    ) -> Estimate:
        self._replicates = replicates
        self._variance = parameters
        return self

    @property
    def replicates(self) -> dict[tuple, list[float]]:
        return self._replicates

    @property
    def variance_parameters(self) -> tuple[int, float] | None:
        return self._variance


class NodeOutcome(_Frozen):
    id: str
    verdict: Verdict
    result: Estimate | None = None  # None for evaluate_request and for a block

    def dump(self) -> dict[str, Any]:
        v = self.verdict
        out: dict[str, Any] = {
            "id": self.id,
            "verdict": v.level,
            "method": v.method,
            "weighted": v.weighted,
        }
        if v.substituted_from:
            out["substituted_from"] = v.substituted_from
        out["messages"] = [m.dump() for m in v.messages]
        out["notes"] = list(v.notes)
        if self.result is not None:
            out["result"] = self.result.model_dump()
        return out
