"""Smoke tests for the CE MCP server.

These verify the server imports and every submodule registers without error.
Add functional tests for your own tools as you build them.
"""

from __future__ import annotations

import pytest
from fastmcp import FastMCP

from ce_mcp_server.app import mcp
from ce_mcp_server.config import settings


def test_server_is_importable():
    assert mcp is not None


def test_server_has_name():
    assert mcp.name == "CE Survey MCP Server"


def test_default_transport_is_stdio():
    """Guard against accidentally shipping with a non-stdio default."""
    assert settings.mcp_transport == "stdio"


def test_tools_register_without_error():
    from ce_mcp_server.tools import register_tools

    register_tools(FastMCP("test"))  # must not raise


def test_prompts_register_without_error():
    from ce_mcp_server.prompts import register_prompts

    register_prompts(FastMCP("test"))  # must not raise


def test_resources_register_without_error():
    from ce_mcp_server.resources import register_resources

    register_resources(FastMCP("test"))  # must not raise


def test_routes_register_without_error():
    from ce_mcp_server.routes import register_routes

    register_routes(FastMCP("test"))  # must not raise


@pytest.mark.asyncio
async def test_the_lookup_tools_are_discoverable():
    """The six lookup tools, the client-model capture tools (no endpoint is
    configured in tests), the two processing tools, and nothing left of the
    template."""
    tools = await mcp.list_tools()
    assert {t.name for t in tools} == {
        "describe_survey",
        "list_datasets",
        "describe_table",
        "search_variables",
        "describe_variable",
        "list_geographies",
        "capture_guide",
        "submit_request_record",
        "evaluate_request",
        "run_request",
    }
