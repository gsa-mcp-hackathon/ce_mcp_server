"""The one capture pipeline of the server process, built on first use.

It shares the metadata service of `service.py`, so the lookup tools and the
capture check read the same sidecar set.
"""

from __future__ import annotations

import threading

from ce_capture.config import CaptureSettings
from ce_capture.context import CaptureContext
from ce_capture.pipeline import CapturePipeline
from ce_capture.storage import open_store
from ce_mcp_server.service import _locate, get_service

_lock = threading.Lock()
_pipeline: CapturePipeline | None = None


def build_pipeline(settings: CaptureSettings | None = None) -> CapturePipeline:
    settings = settings or CaptureSettings()
    ctx = CaptureContext.from_service(get_service())
    store = open_store(settings, records_dir=_locate(settings.records_dir))
    return CapturePipeline(ctx, store)


def get_pipeline() -> CapturePipeline:
    global _pipeline
    if _pipeline is None:
        with _lock:
            if _pipeline is None:
                _pipeline = build_pipeline()
    return _pipeline


def set_pipeline(pipeline: CapturePipeline | None) -> None:
    """Replace the process's pipeline; `None` makes the next call build it again."""
    global _pipeline
    with _lock:
        _pipeline = pipeline
