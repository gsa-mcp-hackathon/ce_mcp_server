"""`search_variables`: columns matching a phrase."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastmcp import FastMCP

from ce_mcp_server.tools._answer import READ_ONLY, answer


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="search_variables",
        annotations={"title": "Search CE Interview variables", **READ_ONLY},
    )
    async def search_variables(
        query: Annotated[
            str, "A column name, a BLS variable name, or words describing it."
        ],
        table: Annotated[
            str | None, "Limit the search to one stored table, such as fmli."
        ] = None,
        year: Annotated[
            int | None, "Survey year to search. Defaults to the release."
        ] = None,
        usable_as: Annotated[
            Literal["measure", "grouping", "filter"] | None,
            "Keep only columns a request can use this way.",
        ] = None,
        limit: Annotated[int, "Most matches to return, 1 to 50."] = 20,
    ) -> dict[str, Any]:
        """Find columns by name or by what they describe, best match first.

        Matches the stored name, the BLS spelling, the description, and the
        labels of a column's codes (so "renter" finds the tenure column, and
        "Toyota vehicle make" the make column). Every table is searched: the
        member table and the detailed expenditure tables too. Each match gives
        its table, role, unit, how a request may use it (`usable_as`), and any
        review note such as "unit under review".

        Columns holding every word of the query come first. After them, or
        alone when no column holds every word, come the closest columns; each
        of those lists the query words it does not hold in `lacks`. Read the
        description of a close match before using it: it is offered, not
        confirmed.

        When capturing a request, search once for each thing the user named,
        in a few plain words, with `usable_as` set to "measure" for what is
        summarized and "grouping" or "filter" for the rest. Take column names
        only from these results. Before deciding that nothing fits, search
        again with another word for the same thing. If nothing suitable comes
        back then, the phrase is unresolved: do not substitute the nearest
        match.
        """
        return await answer(
            lambda service: service.search_variables(
                query, table, year, usable_as, limit
            )
        )
