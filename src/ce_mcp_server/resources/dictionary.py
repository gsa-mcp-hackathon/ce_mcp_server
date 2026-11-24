"""`ce://dictionary/{year}`: the variables of one survey year, by table."""

from __future__ import annotations

from fastmcp import FastMCP

from ce_mcp_server.resources._read import JSON, read
from ce_meta.service import MetadataService


def _listing(service: MetadataService, year: int) -> dict:
    """Table -> stored name -> one-line description: the whole year at a glance.

    Kept to names and descriptions. Roles, units, codes and review state are
    what `search_variables` and `describe_variable` are for.
    """
    return {
        "year": year,
        "tables": {
            table: {m.name: m.description for m in matches}
            for table, matches in service.dictionary(year).items()
        },
    }


def register(mcp: FastMCP) -> None:
    @mcp.resource("ce://dictionary/{year}", mime_type=JSON)
    def dictionary(year: int) -> str:
        """Every CE Interview variable of one survey year, grouped by table.

        The release year is listed from the sidecars. Earlier years come from
        the BLS data dictionary and need the dictionary tables (`ce-meta
        build`). This is a long list: to find a column, prefer the
        `search_variables` tool.
        """
        return read(lambda service: _listing(service, int(year)))
