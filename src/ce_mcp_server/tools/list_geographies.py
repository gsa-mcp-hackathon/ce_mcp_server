"""`list_geographies`: the codes of one geography level and its support."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastmcp import FastMCP

from ce_mcp_server.tools._answer import READ_ONLY, answer


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="list_geographies",
        annotations={"title": "List CE Interview geographies", **READ_ONLY},
    )
    async def list_geographies(
        level: Annotated[
            Literal["region", "division", "state", "psu"],
            "The geography level. psu is the named metropolitan areas.",
        ],
        year: Annotated[
            int | None, "Survey year to count interviews for. Defaults to the release."
        ] = None,
    ) -> dict[str, Any]:
        """The codes of one geography level, and how far the design supports it.

        Returns the table and column that carry the level, each code with its
        label and its unweighted number of interviews in the survey year, and
        the support rating: reliable levels may be estimated, limited levels
        need a caveat, unsupported levels may not be estimated.

        When capturing a request grouped or filtered by place, call this to
        get the column to use and the code for a named place, and to see
        whether the level is supported at all.
        """
        return await answer(lambda service: service.list_geographies(level, year))
