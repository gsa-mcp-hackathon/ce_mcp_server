"""`run_request`: judge and compute a captured request (Plan 5)."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP

from ce_mcp_server.tools._process import WRITES_ONE_RECORD, process


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="run_request",
        annotations={"title": "Compute a CE request", **WRITES_ONE_RECORD},
    )
    async def run_request(
        request_id: Annotated[str, "A captured request's ID, from the capture tools."],
        resolved_id: Annotated[
            str | None, "One of its resolved records; the newest when omitted."
        ] = None,
    ) -> dict[str, Any]:
        """Judge and compute each statistic of a captured request from the CE
        Interview microdata: weighted estimates per group, with replicate-weight
        standard errors where asked. A blocked statistic gives its reason
        instead of a figure.

        Show the `reply` text to the user as written: every caution sits under
        the figure it applies to, and dropping one misstates the result. The
        `outcome` holds the same in structured form and is stored.

        A statistic the server blocks is answered by its reason. Do not
        capture a different request in its place unless the user asks for it;
        say what could be computed instead and let them choose. Do not work
        out figures of your own from the results (a share from counts, an
        interval from a standard error, a total of the groups).
        """
        return await process("run", request_id, resolved_id)
