"""The one request processor of the server process (Plan 5), built on first use.

It shares the metadata service of `service.py` and the record store of the
capture pipeline, so it judges records against the same sidecar set they were
captured with and writes its outcome records beside them.
"""

from __future__ import annotations

import threading

from ce_mcp_server.capture import get_pipeline
from ce_mcp_server.service import get_service
from ce_measures.process import RequestProcessor

_lock = threading.Lock()
_processor: RequestProcessor | None = None


def build_processor() -> RequestProcessor:
    pipeline = get_pipeline()
    data_dir = getattr(get_service().catalog, "data_dir", None)
    return RequestProcessor(pipeline.ctx, pipeline.store, data_dir)


def get_processor() -> RequestProcessor:
    global _processor
    if _processor is None:
        with _lock:
            if _processor is None:
                _processor = build_processor()
    return _processor


def set_processor(processor: RequestProcessor | None) -> None:
    """Replace the process's processor; `None` makes the next call build it again."""
    global _processor
    with _lock:
        _processor = processor
