"""`submit_request_record`: the client-model path of request capture (D27)."""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any

from fastmcp import FastMCP

from ce_capture.pipeline import LinkError, Submission
from ce_capture.records import ModelOutput
from ce_mcp_server.capture import get_pipeline

log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="submit_request_record",
        annotations={
            "title": "Submit a CE request record",
            "readOnlyHint": False,  # stores a record
            "destructiveHint": False,  # never changes or removes one
            "idempotentHint": False,  # each call stores a new record
            "openWorldHint": False,
        },
    )
    async def submit_request_record(
        user_text: Annotated[str, "The user's message, verbatim."],
        session_id: Annotated[
            str, "An ID that stays the same for the whole conversation."
        ],
        record: Annotated[
            ModelOutput,
            "The nodes, answers, ambiguities and unresolved phrases, written "
            "from lookup answers. For a follow-up, a patch instead of nodes.",
        ],
        follows_request_id: Annotated[
            str | None, "For a follow-up: the request it changes."
        ] = None,
        answers_request_id: Annotated[
            str | None, "For the user's answer to a question: the request that asked."
        ] = None,
        corrects_request_id: Annotated[
            str | None, "For a correction: the submission that failed the check."
        ] = None,
        model: Annotated[str | None, "The model writing the record, if known."] = None,
    ) -> dict[str, Any]:
        """Check, store and resolve a request record written by you.

        Call capture_guide first, and take every table and column from the
        lookup tools. The server normalizes names and codes, locates your
        quotes in the user's text, checks the record, and stores it with a
        status:

        - captured: resolved; the answer says how each statistic will be computed.
        - needs_clarification: ask the user the questions as written, then submit
          their answer with answers_request_id.
        - check_failed: correct the record from the errors and submit it again
          with corrects_request_id. A third correction in one chain is refused.
        - not_captured: the message is not a CE Interview statistics request.
        """
        sub = Submission(
            user_text=user_text,
            session_id=session_id,
            output=record.model_dump(mode="json", by_alias=True),
            path="client_model",
            model=model,
            follows_request_id=follows_request_id,
            answers_request_id=answers_request_id,
            corrects_request_id=corrects_request_id,
        )
        try:
            return await asyncio.to_thread(get_pipeline().submit, sub)
        except LinkError as exc:
            return {"error": str(exc), "stored": False}
        except Exception:
            log.exception("submit_request_record failed")
            return {"error": "The record could not be stored. The failure is logged."}
