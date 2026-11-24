"""`list_datasets`: the Interview tables and what is loaded."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP

from ce_mcp_server.tools._answer import READ_ONLY, answer


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="list_datasets",
        annotations={"title": "List the CE Interview tables", **READ_ONLY},
    )
    async def list_datasets(
        year: Annotated[
            int | None, "Survey year to count rows for. Defaults to the release."
        ] = None,
    ) -> dict[str, Any]:
        """The CE Interview tables: what one row of each is, and what is loaded.

        Lists the five core tables (fmli consumer units, memi members, mtbi
        monthly expenditures, itbi income, itii imputed income) and the
        detailed expenditure tables (expn_*), one per questionnaire section,
        each with its title and the number of rows in the survey year. Rows
        are interviews in fmli only.

        When capturing a request, use this to find which table holds a
        subject, then call describe_table for the one you pick.
        """
        return await answer(lambda service: service.list_datasets(year))
