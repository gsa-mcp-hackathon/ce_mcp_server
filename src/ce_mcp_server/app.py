"""MCP server entry point.

Keep this file THIN. It only:
  1. creates the FastMCP instance,
  2. registers tools / prompts / resources / routes from the submodules, and
  3. decides which transport to run (stdio locally vs. HTTP when deployed).

All real logic lives in the submodules:
  - tools/      one file per tool (each exposes `register(mcp)`)
  - prompts/    reusable conversation starters
  - resources/  static/semi-static data the client can read directly
  - routes.py   HTTP-only infrastructure (health check, etc.)
  - config.py   settings loaded from env vars / .env
  - models.py   Pydantic models & enums for tool parameters
  - utils.py    shared helpers (HTTP client, formatting, pagination)
"""

from __future__ import annotations

import os

from fastmcp import FastMCP

from ce_mcp_server.config import settings
from ce_mcp_server.prompts import register_prompts
from ce_mcp_server.resources import register_resources
from ce_mcp_server.routes import register_routes
from ce_mcp_server.tools import register_tools

# The server name is what MCP clients display.
mcp = FastMCP(
    name="CE Survey MCP Server",
    instructions=(
        "Describes the BLS Consumer Expenditure (CE) Interview Survey "
        "public-use microdata (its tables, variables, codes and geographies) "
        "and computes statistics from it: means, totals, counts, ratios and "
        "differences. To find the tables and columns a question "
        "needs, call describe_survey first, then list_datasets and "
        "describe_table for the table, then search_variables and "
        "describe_variable for the columns, and list_geographies for a place. "
        "Take every table and column name from these tools, never from "
        "memory. A column marked as under review is not settled: say so. "
        "To ask for statistics, capture the request first: call capture_guide "
        "once, then submit_request_record with the record you wrote from the "
        "lookups (or capture_request, when the server has its own model). "
        "When the request comes back captured, call run_request with its "
        "request_id to compute it; evaluate_request shows the verdicts without "
        "computing. Show the reply text to the user as written."
    ),
)

# Wire up everything the server exposes. Each aggregator lives in its submodule.
register_tools(mcp)
register_prompts(mcp)
register_resources(mcp)
register_routes(mcp)


def main() -> None:
    """Start the server, choosing a transport based on the environment.

    Precedence:
      1. A platform-injected port (Databricks `DATABRICKS_APP_PORT` or a generic
         `PORT`) → run HTTP on that port. Cloud hosts set this for you.
      2. `MCP_TRANSPORT=streamable-http` in the environment → run HTTP on
         `MCP_HOST`:`MCP_PORT` (see config.py / .env).
      3. Otherwise → stdio, which is how local clients (Claude Desktop, Claude
         Code, Zed) launch the server as a subprocess.
    """
    platform_port = os.getenv("DATABRICKS_APP_PORT") or os.getenv("PORT")

    if platform_port:
        mcp.run(transport="http", host="0.0.0.0", port=int(platform_port))
    elif settings.mcp_transport == "streamable-http":
        mcp.run(
            transport="streamable-http",
            host=settings.mcp_host,
            port=settings.mcp_port,
        )
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
