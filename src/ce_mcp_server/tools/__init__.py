"""Tools submodule — one tool per file.

Each tool module exposes a `register(mcp)` function that attaches its
`@mcp.tool`-decorated function to the server. This file aggregates them into a
single `register_tools(mcp)` that `app.py` calls.

Six are the lookup tools of the metadata service: they read the sidecar set and
the stored tables through `ce_meta` and change nothing. The capture tools
(`ce_capture`, Plan 4) store request records: `capture_guide` and
`submit_request_record` for a client's model, or `capture_request` when the
server has its own model. `evaluate_request` and `run_request` (`ce_measures`,
Plan 5) judge and compute a captured request and store an outcome record.

To add a tool:
  1. Create `tools/<your_tool>.py` with a `register(mcp)` function.
  2. Import it below and call `<your_tool>.register(mcp)` inside register_tools.
"""

from __future__ import annotations

from ce_capture.config import CaptureSettings
from ce_mcp_server.tools import (
    capture_guide,
    capture_request,
    describe_survey,
    describe_table,
    describe_variable,
    evaluate_request,
    list_datasets,
    list_geographies,
    run_request,
    search_variables,
    submit_request_record,
)


def register_tools(mcp) -> None:
    """Register all tools with the MCP server."""
    describe_survey.register(mcp)
    list_datasets.register(mcp)
    describe_table.register(mcp)
    search_variables.register(mcp)
    describe_variable.register(mcp)
    list_geographies.register(mcp)

    # One capture path per deployment (Plan 4): the server's own model when a
    # serving endpoint is configured, the client's model otherwise.
    settings = CaptureSettings()
    server_path = bool(settings.endpoint)
    if server_path or settings.both_paths:
        capture_request.register(mcp)
    if not server_path or settings.both_paths:
        capture_guide.register(mcp)
        submit_request_record.register(mcp)

    # Processing of captured requests (Plan 5): judge, then compute.
    evaluate_request.register(mcp)
    run_request.register(mcp)


__all__ = ["register_tools"]
