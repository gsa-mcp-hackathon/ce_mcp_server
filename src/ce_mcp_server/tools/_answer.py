"""What every lookup tool does around its one service call."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from ce_mcp_server.service import get_service
from ce_meta.errors import CeMetaError
from ce_meta.service import MetadataService

log = logging.getLogger(__name__)

# Shared by every lookup tool: they read sidecar files and stored tables on the
# server and change nothing.
READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


async def answer(call: Callable[[MetadataService], BaseModel]) -> dict[str, Any]:
    """Run one service call off the event loop and return it as plain JSON.

    A `CeMetaError` becomes an error the model can act on, with suggestions.
    Anything else is logged and answered with a generic message, so no tool
    returns a stack trace. Empty and default fields are left out to keep the
    answer small.
    """
    try:
        result = await asyncio.to_thread(lambda: call(get_service()))
    except CeMetaError as exc:
        return {"error": exc.message, "suggestions": exc.suggestions}
    except Exception:
        log.exception("a lookup tool failed")
        return {
            "error": "The metadata service could not answer. "
            "The failure is logged on the server."
        }
    return result.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
