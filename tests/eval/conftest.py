"""Fixtures for the evaluation package: the real case sets on the real
sidecars (no data, as in CI), and a small case set on the tiny stored tables,
on which a scripted model can be run end to end."""

from __future__ import annotations

from pathlib import Path

import pytest

from ce_capture.context import CaptureContext
from ce_eval.cases import CaseSet, _case, load_cases
from ce_mcp_server.service import build_service
from ce_meta.config import MetaSettings
from tests.measures.conftest import tiny  # noqa: F401

ROOT = Path(__file__).parents[2]
CASES = ROOT / "eval" / "cases"
DATA = ROOT / "data" / "parquet"
AS_RELEASED = {"gap_2025": {"value": "as_released", "evidence": "as released"}}
PERIOD = {"text": "in 2025", "kind": "year", "values": [2025]}


@pytest.fixture(scope="session")
def real_ctx() -> CaptureContext:
    settings = MetaSettings(sidecar_dir=ROOT / "sidecars", data_dir=ROOT / "no-data")
    return CaptureContext.from_service(build_service(settings))


@pytest.fixture(scope="session")
def cases() -> CaseSet:
    return load_cases(CASES)


def _node(**more) -> dict:
    node = {
        "id": "n1",
        "kind": "estimate",
        "statistic": "mean",
        "measure": {"table": "fmli", "column": "totexpcq"},
        "period": PERIOD,
        "stated": dict(AS_RELEASED),
    }
    node.update(more)
    return node


TINY_CASES = [
    {
        "id": "by-region",
        "nuance": ["region_c"],
        "message": "Average total spending per household by region in 2025, "
        "as released.",
        "gold": {
            "nodes": [_node(group_by=[{"table": "fmli", "column": "region"}])],
            "answers": [
                {"asks": "Average total spending per household by region", "node": "n1"}
            ],
        },
        "expect": {
            "status": "captured",
            "outcome": {"n1": {"verdict": "warn", "rules": ["region_code_c"]}},
        },
    },
    {
        "id": "renters",
        "follows": "by-region",
        "nuance": ["follow_up"],
        "message": "Only households that rent.",
        "gold": {
            "patch": [
                {
                    "op": "add_filter",
                    "node": "*",
                    "filter": {
                        "table": "fmli",
                        "column": "cutenure",
                        "comparison": "eq",
                        "value": "4",
                    },
                }
            ],
            "answers": [],
        },
        "expect": {"status": "captured"},
    },
    {
        "id": "member-age",
        "nuance": ["member_level"],
        "message": "What was the average age of household members in 2025, "
        "as released?",
        "gold": {
            "nodes": [_node(measure={"table": "memi", "column": "age"})],
            "answers": [
                {"asks": "What was the average age of household members", "node": "n1"}
            ],
        },
        "expect": {
            "status": "captured",
            "outcome": {"n1": {"verdict": "block", "rules": ["member_level"]}},
        },
    },
    {
        "id": "income-no-choice",
        "nuance": ["gap_2025"],
        "message": "What was average income before taxes per household in 2025?",
        "gold": {
            "nodes": [
                _node(measure={"table": "fmli", "column": "fincbtxm"}, stated={})
            ],
            "answers": [
                {
                    "asks": "What was average income before taxes per household",
                    "node": "n1",
                }
            ],
        },
        "expect": {"status": "needs_clarification"},
    },
    {
        "id": "income-as-released",
        "answers": "income-no-choice",
        "nuance": ["clarification"],
        "message": "As released.",
        "gold": {
            "nodes": [
                _node(
                    measure={"table": "fmli", "column": "fincbtxm"},
                    stated={
                        "gap_2025": {"value": "as_released", "evidence": "As released"}
                    },
                )
            ],
            "answers": [
                {
                    "asks": "What was average income before taxes per household",
                    "node": "n1",
                }
            ],
        },
        "expect": {"status": "captured"},
    },
    {
        "id": "who-runs-it",
        "nuance": ["not_statistical"],
        "message": "Who runs the Consumer Expenditure Survey?",
        "gold": {
            "nodes": [],
            "reason_code": "not_statistical",
            "reason": "A question about the survey, not a statistic.",
        },
        "expect": {"status": "not_captured"},
    },
]


@pytest.fixture
def tiny_cases() -> CaseSet:
    return CaseSet(tuple(_case(raw, "core") for raw in TINY_CASES), (2025,), {})
