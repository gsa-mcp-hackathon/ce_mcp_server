"""Resources submodule — static/semi-static data the client can read directly.

Resources are addressable by URI and don't require a tool call. Each resource
module exposes a `register(mcp)` function; this file aggregates them into
`register_resources(mcp)`.

The three here are thin calls to the metadata service: the variables of a
year, the geography codes, and the weighting method.
"""

from __future__ import annotations

from ce_mcp_server.resources import dictionary, geography, methods


def register_resources(mcp) -> None:
    """Register all resources with the MCP server."""
    dictionary.register(mcp)
    geography.register(mcp)
    methods.register(mcp)


__all__ = ["register_resources"]
