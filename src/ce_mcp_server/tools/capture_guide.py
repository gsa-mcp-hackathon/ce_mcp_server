"""`capture_guide`: what a client's model reads before it writes a record."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastmcp import FastMCP

from ce_capture.guide import capture_guide as build_guide
from ce_mcp_server.capture import get_pipeline
from ce_mcp_server.tools._answer import READ_ONLY


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="capture_guide",
        annotations={"title": "How to capture a CE request", **READ_ONLY},
    )
    async def capture_guide(
        include_schema: Annotated[
            bool,
            "Also return the record's JSON Schema. It is the input schema of "
            "submit_request_record, so it is usually not needed here.",
        ] = False,
    ) -> dict[str, Any]:
        """The instructions for turning a user's request into a request record.

        Call this once in a session, before the first request for CE Interview
        statistics. It returns the capture instructions (the steps, the rules,
        the record's fields and an example), the tables capture covers, and the
        survey years loaded. Then use the lookup tools to find every table and
        column, and submit the record with submit_request_record.
        """
        pipeline = get_pipeline()
        return await asyncio.to_thread(build_guide, pipeline.ctx, include_schema)
