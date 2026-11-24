"""The one `MetadataService` of the server process.

Built on first use, so importing the server stays cheap and a client that only
lists the tools pays nothing. The sidecar set is loaded and checked once. A
failed check does not stop the server: it is logged here, and the service adds
a notice to the answers it affects.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from ce_meta.catalog import get_catalog
from ce_meta.config import MetaSettings
from ce_meta.service import MetadataService
from ce_meta.sidecars import load_set, validate

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_lock = threading.Lock()
_service: MetadataService | None = None


def _locate(path: Path) -> Path:
    """A relative path as given when it exists there, else under the project.

    A local client starts the server from its own working directory, so the
    default `sidecars` and `data/parquet` folders are also looked for beside
    the source tree.
    """
    if path.is_absolute() or path.exists():
        return path
    return PROJECT_ROOT / path


def build_service(settings: MetaSettings | None = None) -> MetadataService:
    settings = settings or MetaSettings()
    settings = settings.model_copy(
        update={
            "data_dir": _locate(settings.data_dir),
            "sidecar_dir": _locate(settings.sidecar_dir),
        }
    )
    sidecars = load_set(settings.sidecar_dir)
    for problem in sidecars.problems:
        log.warning("sidecar file %s: %s", problem.file, problem.detail)
    report = validate(sidecars)
    for check in report.checks:
        for failure in check.failures:
            where = "set" if failure.scope == "set" else f"table {failure.table}"
            log.warning(
                "sidecar check %s failed [%s]: %s", check.check, where, failure.detail
            )
    return MetadataService(get_catalog(settings), sidecars, report, settings.release)


def get_service() -> MetadataService:
    global _service
    if _service is None:
        with _lock:
            if _service is None:
                _service = build_service()
    return _service


def set_service(service: MetadataService | None) -> None:
    """Replace the process's service; `None` makes the next call build it again."""
    global _service
    with _lock:
        _service = service
