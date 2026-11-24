"""What every resource does around its one service call."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from ce_mcp_server.service import get_service
from ce_meta.errors import CeMetaError
from ce_meta.service import MetadataService

log = logging.getLogger(__name__)
JSON = "application/json"


def read(call: Callable[[MetadataService], Any]) -> str:
    """Run one service call and return it as JSON text.

    A resource has no way to return a structured error, so a failure is
    returned as a JSON object with an `error`, never as a stack trace.
    """
    try:
        result = call(get_service())
    except CeMetaError as exc:
        result = {"error": exc.message, "suggestions": exc.suggestions}
    except Exception:
        log.exception("a resource failed")
        result = {"error": "The metadata service could not answer."}
    return json.dumps(result, ensure_ascii=False)


def dump(model: Any) -> dict:
    return model.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
