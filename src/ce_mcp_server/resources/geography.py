"""`ce://geography/codes`: every geography level, its codes and its support."""

from __future__ import annotations

from fastmcp import FastMCP

from ce_mcp_server.resources._read import JSON, dump, read
from ce_meta.service import GEOGRAPHY_VALUE, MetadataService


def _codes(service: MetadataService) -> dict:
    return {level: dump(service.list_geographies(level)) for level in GEOGRAPHY_VALUE}


def register(mcp: FastMCP) -> None:
    @mcp.resource("ce://geography/codes", mime_type=JSON)
    def geography_codes() -> str:
        """The region, division, state and PSU codes of the CE Interview Survey.

        For each level: the column that carries it, each code with its label,
        its parent geographies and its unweighted interviews in the release
        year, and how far the sample design supports estimates at that level
        (reliable, limited or unsupported).
        """
        return read(_codes)
