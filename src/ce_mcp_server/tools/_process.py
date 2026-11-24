"""What both processing tools do around their one processor call.

The processor is imported on the first call, so starting the server loads
neither pyarrow nor NumPy (the import guard in tests/server/test_tools.py).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from ce_measures.errors import CeMeasuresError, NotProcessable
from ce_measures.outcome import dump

log = logging.getLogger(__name__)

# Both read the stored data and write one outcome record; they change no
# other record and remove nothing.
WRITES_ONE_RECORD = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,  # each call writes a new outcome record
    "openWorldHint": False,
}


async def process(
    mode: Literal["evaluate", "run"], request_id: str, resolved_id: str | None
) -> dict[str, Any]:
    def call():
        from ce_mcp_server.measures import get_processor

        processor = get_processor()
        fn = processor.evaluate if mode == "evaluate" else processor.run
        return fn(request_id, resolved_id)

    try:
        record, text = await asyncio.to_thread(call)
    except NotProcessable as exc:
        return {
            "error": exc.message,
            "status": exc.status,
            "reason": exc.reason,
            "stored": False,
        }
    except CeMeasuresError as exc:
        return {"error": exc.message, "stored": False}
    except Exception:
        log.exception("%s_request failed", mode)
        return {"error": "The request could not be processed. The failure is logged."}
    outcome = dump(record)
    outcome.pop("reply", None)
    return {"reply": text, "outcome": outcome}
