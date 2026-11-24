# CE Survey MCP Server — BLS Consumer Expenditure Surveys

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server in Python for BLS Consumer Expenditure Surveys (CE) data, built for the GSA MCP hackathon from its server template. It ships a loader for the CE public-use microdata (`ce_ingest`) and deployment kits for **IBM Cloud (watsonx Orchestrate)** and **Databricks**.

Built with [FastMCP](https://github.com/jlowin/fastmcp) and [uv](https://docs.astral.sh/uv/). If you have never built an MCP server before, start with **[QUICKSTART.md](QUICKSTART.md)**.

---

## What is an MCP server?

An MCP server exposes **tools** (functions the model can call), **prompts** (reusable conversation starters), and **resources** (data the model can read) to an AI client such as Claude Desktop, Claude Code, or an agent platform like watsonx Orchestrate. You write the tools; the client's model decides when to call them.

This template gives you a working server with one example of each, so you can replace the examples with your own service and deploy.

---

## Repo structure

```
mcp-hackathon/
├── README.md                  # This file
├── QUICKSTART.md              # 5-minute clone → run → connect walkthrough
├── main.py                    # Local entry point (uv run python main.py)
├── pyproject.toml             # Package + dependencies (uv)
├── requirements.txt           # Mirror of runtime deps (for buildpack hosts)
├── Dockerfile                 # Container image (streamable-HTTP, port 8080)
├── manifest.yaml              # cloud.gov (Cloud Foundry) deploy
├── server.json                # MCP registry metadata
├── .env.example               # Copy to .env for local dev
├── .github/workflows/ci.yml   # Lint + test on push/PR
├── docs/                      # Vendor-contributed reference materials
├── src/
│   ├── ce_ingest/             # CE PUMD loader (SAS → partitioned Parquet/Delta), `ce-ingest` CLI
│   ├── ce_meta/               # Read-only metadata service (not yet built)
│   └── ce_mcp_server/         # The MCP server
│       ├── app.py             # Thin entry point: builds FastMCP, picks transport
│       ├── config.py          # Settings from env vars / .env
│       ├── models.py          # Pydantic models & enums for tool params
│       ├── utils.py           # Shared helpers (HTTP client, pagination)
│       ├── routes.py          # HTTP-only routes (/health, /version)
│       ├── tools/             # ONE FILE PER TOOL
│       │   ├── __init__.py    #   register_tools(mcp) aggregator
│       │   ├── describe_survey.py, list_datasets.py, describe_table.py,
│       │   └── search_variables.py, describe_variable.py, list_geographies.py
│       ├── service.py         # The one MetadataService of the process
│       ├── prompts/
│       │   └── __init__.py    #   register_prompts(mcp) aggregator (none yet)
│       └── resources/
│           ├── __init__.py    #   register_resources(mcp) aggregator
│           └── dictionary.py, geography.py, methods.py
├── tests/                     # Server smoke tests; tests/ingest/ covers the loader
├── eval/                      # Stub → build a Phoenix eval harness (see mcp-eval skill)
└── deploy/
    ├── README.md              # Which deployment kit to use
    ├── ibm/                   # watsonx Orchestrate: 3 kits (see below)
    └── databricks/            # Databricks Apps kit
```

---

## Getting started

### Prerequisites
- [uv](https://docs.astral.sh/uv/) — `pip install uv` or `brew install uv`

### Install and run

```bash
cp .env.example .env
uv sync
uv run python main.py
```

The server starts in **stdio** mode — it talks JSON-RPC over stdin/stdout, which is how local clients (Claude Desktop, Claude Code) launch it. See [QUICKSTART.md](QUICKSTART.md) to connect a client.

### Verify

```bash
uv sync --group dev
uv run pytest tests/ -v      # tests
uv run ruff check .          # lint
```

---

## The one-tool-per-file pattern

Each tool lives in its own file under `src/ce_mcp_server/tools/` and exposes a `register(mcp)` function. `tools/__init__.py` calls each one from a single `register_tools(mcp)`. This keeps the tool list scannable and lets you add or remove an integration by touching two files.

**Step 1 — create `src/ce_mcp_server/tools/my_tool.py`:**

```python
from typing import Annotated
from urllib.parse import quote
from fastmcp import FastMCP

from ce_mcp_server.utils import fetch_json


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="example_get_thing",
        annotations={
            "title": "Get a thing",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def get_thing(thing_id: Annotated[str, "The ID to fetch."]) -> dict:
        """One-line summary. Document the data source, its update cadence,
        and the return shape here — the model reads this docstring."""
        safe_id = quote(thing_id, safe="")
        return await fetch_json(f"things/{safe_id}")
```

**Step 2 — wire it up in `tools/__init__.py`:**

```python
from ce_mcp_server.tools import describe_survey, my_tool  # and the others

def register_tools(mcp) -> None:
    describe_survey.register(mcp)
    my_tool.register(mcp)  # ← add this line
```

**Step 3 — add any API key** as a typed field in `config.py` and document the env var in `.env.example`.

Prompts (`prompts/`) and resources (`resources/`) follow the exact same `register(mcp)` + aggregator pattern.

---

## Packages

The project is `ce-mcp` (`pyproject.toml`). It holds three packages:

| Package | What it is | Entry point |
| --- | --- | --- |
| `ce_mcp_server` | The MCP server | `uv run ce-mcp-server` (or `uv run python main.py`) |
| `ce_ingest` | Loader for BLS CE public-use microdata: SAS files → hive-partitioned Parquet (Delta on Databricks), plus registry and column profiles | `uv run ce-ingest files \| load \| profile` |
| `ce_meta` | Read-only metadata service | Not yet built |

The loader's dependencies are an optional extra, so the server image doesn't carry them: `uv sync --extra ingest --group dev`. The server never imports `ce_ingest`.

---

## Tool design tips (federal data)

- **Return structured data, not prose.** Return dicts/lists with consistent keys and let the model narrate.
- **Document freshness.** Federal datasets lag; state the update frequency and "as-of" date in the docstring.
- **Expose pagination.** Use `PaginationParams` / `paginate()` from `utils.py`, and return `has_more` / `next_offset`.
- **Use explicit timeouts.** `utils.fetch_json` defaults to 30s.
- **Actionable errors.** Return an error dict with a `hint`, not a raw stack trace.
- **Constrain outbound requests.** Keep API origins in operator-controlled code or configuration, pass only validated relative paths to `fetch_json`, and encode path segments. Never pass a tool-supplied URL directly to an HTTP client.
- **Treat redirects and DNS as security boundaries.** Redirects are disabled by default. A server that must fetch caller-supplied URLs needs connection-time IP validation on every hop plus network egress controls; a one-time DNS check is not sufficient.
- **Keep sensitive data out of errors and logs.** Do not expose or log upstream bodies, headers, full URLs, query strings, credentials, stack traces, SSNs, dates of birth, or addresses.

---

## Deploying

Local development uses stdio. To share your server with an agent platform, deploy it and register it. See **[deploy/README.md](deploy/README.md)** for a chooser, then:

- **IBM watsonx Orchestrate** — [deploy/ibm/](deploy/ibm/) (three kits: local stdio toolkit, Code Engine build-from-Git, and prebuilt image).
- **Databricks Apps** — [deploy/databricks/](deploy/databricks/).

Both read the same server code; `app.py` automatically serves HTTP when the platform injects a port.

---

## Partner reference materials

The [`docs/`](docs/) directory contains educational reference materials that
IBM and Databricks may contribute for hackathon participants. Vendor-contributed
materials do not represent the views, positions, or policies of GSA or the
United States Government.

GSA does not endorse any commercial product, service, or enterprise referenced
in these materials. Reference to a commercial product, service, or enterprise
does not constitute or imply endorsement, recommendation, or favoring by GSA or
the United States Government. These materials are provided for educational
purposes only. See [`docs/README.md`](docs/README.md) for the complete notice and
contribution guidance.

---

## Evaluations

Measuring how well an LLM can use your tools is the real test of server quality. This template intentionally does **not** ship an eval harness — see [eval/README.md](eval/README.md) for how to build one with the `mcp-eval` skill.

---

## License

[MIT](LICENSE). See [SECURITY.md](SECURITY.md) for the vulnerability disclosure policy and hackathon security notes.
