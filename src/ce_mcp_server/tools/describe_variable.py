"""`describe_variable`: everything known about one column."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP

from ce_mcp_server.tools._answer import READ_ONLY, answer


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="describe_variable",
        annotations={"title": "Describe one CE Interview variable", **READ_ONLY},
    )
    async def describe_variable(
        name: Annotated[str, "Stored column name or BLS spelling, in any casing."],
        table: Annotated[
            str | None, "The stored table, when the name is in more than one."
        ] = None,
        year: Annotated[
            int | None, "Survey year to describe. Defaults to the release."
        ] = None,
        code_query: Annotated[
            str | None,
            "Return only the codes whose value starts with this or whose label "
            "contains these words.",
        ] = None,
        code_limit: Annotated[int, "Most codes to return, 1 to 200."] = 50,
    ) -> dict[str, Any]:
        """The definition, codes, facts and caveats of one column.

        Returns the description, the codes and their labels, the role, unit
        and level, how a request may use the column, the other quarter column
        of a previous/current pair, its flag column, its set of imputed
        versions, the caveats that follow from its facts, and what is still
        under review. A long code list is cut: use `code_query` to find a code
        by its label, for example an item code for "rent" in mtbi.ucc.

        When capturing a request, call this to confirm a column before using
        it, and to turn a label the user gave ("Northeast") into its code.
        """
        return await answer(
            lambda service: service.describe_variable(
                name, table, year, code_query, code_limit
            )
        )
