# Quickstart — Your First MCP Server in 5 Minutes

New to MCP? This gets you from clone to a running server connected to a client.

## 1. Install prerequisites

You need [uv](https://docs.astral.sh/uv/) (a fast Python package manager):

```bash
pip install uv     # or: brew install uv
```

## 2. Set up the project

```bash
cp .env.example .env
uv sync
```

## 3. Run the server

```bash
uv run python main.py
```

The server starts in **stdio** mode and waits for a client to connect over
stdin/stdout. That's correct — it doesn't print a URL, because a local MCP
server is launched *by* the client as a subprocess. Press `Ctrl-C` to stop it.

## 4. Connect a client

### Claude Code

Create or edit `.mcp.json` (or your user MCP settings) with the **absolute path**
to this repo:

```json
{
  "mcpServers": {
    "ce-mcp": {
      "command": "uv",
      "args": ["run", "ce-mcp-server"],
      "cwd": "/absolute/path/to/mcp-hackathon"
    }
  }
}
```

### Claude Desktop

Add the same block to
`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or
`%APPDATA%\Claude\claude_desktop_config.json` (Windows), then restart Claude
Desktop.

Once connected, ask the client a question the lookup tools can answer, e.g.:

> "Which CE Interview column holds total expenditures, and what is region code C?"

The client calls `search_variables` and `describe_variable`. The answers come from
the sidecar files in `sidecars/`, so this works before any data is loaded; row and
interview counts appear once `ce-ingest load` has run.

## 5. Verify everything

```bash
uv sync --group dev
uv run pytest tests/ -v      # tests pass
uv run ruff check .          # lint clean
```

## 6. Test HTTP mode (optional)

Cloud hosts run the server over HTTP. Try it locally:

```bash
MCP_TRANSPORT=streamable-http uv run python main.py
# in another terminal:
curl http://localhost:8000/health      # {"status":"healthy",...}
```

---

## Next steps

- **Add a real tool** — follow "The one-tool-per-file pattern" in the
  [README](README.md#the-one-tool-per-file-pattern).
- **Load the CE data** — `uv sync --extra ingest --group dev`, then
  `uv run ce-ingest load --year 2025`; see [Packages](README.md#packages) in the
  README.
- **Deploy it** — pick a kit in [deploy/README.md](deploy/README.md)
  (IBM watsonx Orchestrate or Databricks).
- **Evaluate it** — build an eval harness with the `mcp-eval` skill; see
  [eval/README.md](eval/README.md).
