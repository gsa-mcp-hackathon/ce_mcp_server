"""`capture_request`: the server-side path of request capture.

Registered only when a Databricks serving endpoint is configured
(CE_CAPTURE_ENDPOINT). The server's own model reads the capture instructions,
uses the lookup tools, and writes the record; this tool returns what was
stored.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any

from fastmcp import FastMCP

from ce_capture.agent import ServerCapture
from ce_capture.config import CaptureSettings
from ce_capture.databricks import DatabricksEndpoint
from ce_capture.model import ModelError
from ce_capture.pipeline import LinkError
from ce_mcp_server.capture import get_pipeline

log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="capture_request",
        annotations={
            "title": "Capture a CE request",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,  # calls the server's model
        },
    )
    async def capture_request(
        user_text: Annotated[str, "The user's message, verbatim."],
        session_id: Annotated[
            str, "An ID that stays the same for the whole conversation."
        ],
        follows_request_id: Annotated[
            str | None, "For a follow-up: the request it changes."
        ] = None,
        answers_request_id: Annotated[
            str | None, "For the user's answer to a question: the request that asked."
        ] = None,
    ) -> dict[str, Any]:
        """Turn a user's request for CE Interview statistics into a stored record.

        Pass the user's message as written. The server's model finds the tables
        and columns, the server checks and stores the record, and the answer
        gives its status: captured (with how each statistic will be computed),
        needs_clarification (ask the user the questions as written, then call
        again with answers_request_id), check_failed, or not_captured.
        If the server's model cannot be reached, the answer is an error and
        nothing is stored.
        """
        settings = CaptureSettings()

        def run() -> dict[str, Any]:
            model = DatabricksEndpoint(settings.endpoint or "", settings)
            agent = ServerCapture(
                get_pipeline(), model, settings.max_lookups, settings.retries
            )
            return agent.capture(
                user_text, session_id, follows_request_id, answers_request_id
            )

        try:
            return await asyncio.to_thread(run)
        except LinkError as exc:
            return {"error": str(exc), "stored": False}
        except ModelError as exc:
            return {"error": str(exc), "stored": False}
        except Exception:
            log.exception("capture_request failed")
            return {
                "error": "The request could not be captured. The failure is logged."
            }
