"""`describe_table`: one table's row meaning, key and route to the weight."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP

from ce_mcp_server.tools._answer import READ_ONLY, answer


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="describe_table",
        annotations={"title": "Describe one CE Interview table", **READ_ONLY},
    )
    async def describe_table(
        table: Annotated[str, "Stored table name, such as fmli, mtbi or expn_rnt."],
    ) -> dict[str, Any]:
        """What one row of a table is, its row key, and how it reaches the weight.

        Returns the table's purpose and limitations, the columns that identify
        a row, whether it carries the sampling weight or reaches it through
        fmli (and whether an interview with no rows in it counts as zero), its
        period columns, and how many of its columns are still under review.

        When capturing a request, call this for each table you intend to use,
        before searching its columns. Never write a join yourself: the route
        to the weight given here is the only one.
        """
        return await answer(lambda service: service.describe_table(table))
