"""Fixtures for request capture: a context on the small fixture set, a store,
a pipeline, and a builder for model output."""

import copy
from pathlib import Path

import pytest

from ce_capture.context import CaptureContext
from ce_capture.pipeline import CapturePipeline, Submission
from ce_capture.storage import JsonFileStore
from ce_mcp_server.service import build_service
from ce_meta.config import MetaSettings

ROOT = Path(__file__).parents[2]
TEXT = (
    "For 2025, as released, what's average spending per household by region? "
    "Skip the weights."
)


@pytest.fixture
def ctx(meta_service) -> CaptureContext:
    return CaptureContext.from_service(meta_service)


@pytest.fixture(scope="session")
def real_ctx() -> CaptureContext:
    """The real sidecar set, with no data loaded (as in CI)."""
    settings = MetaSettings(
        sidecar_dir=ROOT / "sidecars", data_dir=ROOT / "no-data-here"
    )
    return CaptureContext.from_service(build_service(settings))


@pytest.fixture
def store(tmp_path) -> JsonFileStore:
    return JsonFileStore(tmp_path / "records")


@pytest.fixture
def pipeline(ctx, store) -> CapturePipeline:
    return CapturePipeline(ctx, store)


def node(**changes) -> dict:
    """An estimate node: mean of totexpcq by region in 2025, as released."""
    base = {
        "id": "n1",
        "kind": "estimate",
        "statistic": "mean",
        "measure": {"table": "fmli", "column": "totexpcq"},
        "group_by": [{"table": "fmli", "column": "region"}],
        "filters": [],
        "period": {"text": "For 2025", "kind": "year", "values": [2025]},
        "stated": {
            "gap_2025": {"value": "as_released", "evidence": "as released"},
            "weighted": {"value": False, "evidence": "Skip the weights"},
        },
    }
    base.update(changes)
    return base


def output(*nodes, **extra) -> dict:
    out = {
        "nodes": list(nodes) or [node()],
        "answers": [
            {"asks": "what's average spending per household by region", "node": "n1"}
        ],
        "ambiguities": [],
        "unresolved": [],
    }
    out.update(extra)
    return copy.deepcopy(out)


def submission(out: dict | None = None, text: str = TEXT, **links) -> Submission:
    return Submission(text, links.pop("session_id", "s1"), out or output(), **links)
