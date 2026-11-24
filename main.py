"""Root entry point for local development.

Run locally:
    uv run python main.py

Or via the installed script:
    uv run ce-mcp-server
"""

from ce_mcp_server.app import main

if __name__ == "__main__":
    main()
