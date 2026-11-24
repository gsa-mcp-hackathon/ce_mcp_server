"""The outcome record: one per run, never changed (P7, pipeline step 6).

It names the request and resolved records it judged, the rule tables' version
and the sidecar versions, so what was judged and under which rules is never in
doubt. A replay under new rules writes a new record.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ce_capture.storage import RecordStore
from ce_meta.sidecars import Report

# Raise it whenever a rule table changes; a test fails if the tables change
# without it (`rules_fingerprint`).
RULES_VERSION = "1.1"
SCHEMA_VERSION = "1.0"
SCHEMA_FILE = Path(__file__).with_name("schemas") / "outcome-record.schema.json"

Level = Literal["pass", "warn", "substitute", "block"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class OutcomeMessage(_Model):
    level: Level
    rule: str
    column: str | None = None
    property: str | None = None
    text: str
    from_: list[str] | None = Field(default=None, alias="from")


class OutcomeGroup(_Model):
    group: dict[str, str | None]
    value: float | None
    standard_error: float | None = None
    interviews: int
    units: int


class OutcomeResult(_Model):
    groups: list[OutcomeGroup]
    unit: str | None
    removed_missing: int = 0
    removed_null_mo_scope: int = 0
    removed_null_value: int = 0
    dropped_no_rows: int = 0
    standard_error_method: str | None = None
    notes: list[str] = []


class OutcomeNode(_Model):
    id: str
    verdict: Level
    method: str | None
    weighted: bool
    substituted_from: str | None = None
    messages: list[OutcomeMessage]
    notes: list[str]
    result: OutcomeResult | None = None


class FailedCheck(_Model):
    check: str
    scope: Literal["set", "table"]
    table: str | None = None
    detail: str


class SidecarReport(_Model):
    passed: bool
    failures: list[FailedCheck] = []


class OutcomeRecord(_Model):
    schema_version: str = SCHEMA_VERSION
    outcome_id: str
    request_id: str
    resolved_id: str
    evaluated_at: str
    mode: Literal["evaluate", "run"]
    rules_version: str
    verdict: Level  # the worst verdict across the nodes, for the index
    sidecars: dict[str, Any]
    sidecar_report: SidecarReport
    nodes: list[OutcomeNode]
    says: list[str]
    reply: str = ""


def new_outcome_id() -> str:
    return f"out-{uuid.uuid4().hex[:12]}"


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def report_summary(report: Report) -> SidecarReport:
    failures = [
        FailedCheck(check=c.check, scope=f.scope, table=f.table, detail=f.detail)
        for c in report.checks
        for f in c.failures
    ]
    return SidecarReport(passed=not failures, failures=failures)


def says(
    pair_request: dict, nodes: list[dict], estimate_years: list[int], gaps: set[str]
) -> list[str]:
    """The statements that go with any result."""
    out = ["Interview Survey only; results will not match BLS published tables"]
    weighted = {n["weighted"] for n in nodes}
    if weighted == {True}:
        out.append("All estimates are weighted with the final sampling weight")
    elif weighted == {False}:
        out.append(
            "No estimate is weighted: the figures describe the interviewed sample"
        )
    if estimate_years:
        ys = sorted(set(estimate_years))
        text = "Survey year " + ", ".join(str(y) for y in ys)
        if 2025 in ys and gaps:
            text += ", " + " and ".join(
                {
                    "as_released": "as released",
                    "adjusted": "adjusted to mimic the published estimates",
                }[g]
                for g in sorted(gaps)
            )
        out.append(text)
    return out


def build(
    *,
    request: dict,
    resolved: dict,
    nodes: list[dict],
    report: Report,
    says_list: list[str],
    mode: Literal["evaluate", "run"],
) -> OutcomeRecord:
    worst = max(
        (n["verdict"] for n in nodes),
        key=["pass", "warn", "substitute", "block"].index,
        default="pass",
    )
    return OutcomeRecord(
        outcome_id=new_outcome_id(),
        request_id=request["request_id"],
        resolved_id=resolved["resolved_id"],
        evaluated_at=now(),
        mode=mode,
        rules_version=RULES_VERSION,
        verdict=worst,
        sidecars=resolved.get("sidecars") or request.get("sidecars") or {},
        sidecar_report=report_summary(report),
        nodes=[OutcomeNode.model_validate(n) for n in nodes],
        says=says_list,
    )


def write(store: RecordStore, record: OutcomeRecord) -> str:
    return store.write("outcome", dump(record))


def dump(record: OutcomeRecord) -> dict:
    return record.model_dump(mode="json", by_alias=True, exclude_none=True)


def schema_text() -> str:
    schema = OutcomeRecord.model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "CE outcome record"
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def write_schema(path: Path = SCHEMA_FILE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(schema_text())
    return path


def rules_fingerprint() -> str:
    """A hash of every rule table and flag outcome, for the version guard."""
    from ce_measures.combine import COMBINE_RULES
    from ce_measures.registry import RULE_TABLES
    from ce_measures.verdict import FLAG_OUTCOMES, SE_RULES, SHARED_RULES

    tables = {
        "shared": SHARED_RULES,
        "standard_error": SE_RULES,
        "combine": COMBINE_RULES,
        **RULE_TABLES,
    }
    doc = {
        name: [r.model_dump(mode="json") for r in rules]
        for name, rules in tables.items()
    }
    doc["flags"] = {k: list(v) for k, v in FLAG_OUTCOMES.items()}
    text = json.dumps(doc, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()[:16]
