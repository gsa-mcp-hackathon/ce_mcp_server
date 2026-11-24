"""`describe_survey`: the survey facts every request depends on."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from ce_mcp_server.tools._answer import READ_ONLY, answer


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="describe_survey",
        annotations={"title": "Describe the CE Interview Survey", **READ_ONLY},
    )
    async def describe_survey() -> dict[str, Any]:
        """The facts every CE Interview request depends on. Call this first.

        Returns the sampling weight and the table that carries it, the sample
        design, the reference period of an interview, how far the design
        supports each geography level (reliable, limited, unsupported), the
        breaks in the series, and the constraints a result must respect, such
        as stating whether a 2025 estimate is as released or adjusted. Each
        fact comes with what follows from it.

        When capturing a request, read this before choosing tables or columns:
        it tells you which choices the user must state and which groupings the
        survey cannot support.
        """
        return await answer(lambda service: service.describe_survey())
