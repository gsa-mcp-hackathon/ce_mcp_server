"""`ce://methods/weighting`: how CE Interview estimates are weighted."""

from __future__ import annotations

from fastmcp import FastMCP

from ce_mcp_server.resources._read import JSON, dump, read


def register(mcp: FastMCP) -> None:
    @mcp.resource("ce://methods/weighting", mime_type=JSON)
    def weighting() -> str:
        """The weight, the replicate weights, the variance method and the
        reference period of the CE Interview Survey, read from the master
        sidecar, each with what the vocabulary says follows from it."""
        return read(lambda service: dump(service.weighting_method()))
